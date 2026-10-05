if isClient() then return end

require "Farming/SFarmingSystem"
require "Farming/SPlantGlobalObject"
local Queue = require "Farming/ApocalypseBRFarmQueue"
local MODULE = "ApocalypseBR_FarmingSystem"
if SFarmingSystem.ApocalypseBRQueuePatchInstalled then return end
SFarmingSystem.ApocalypseBRQueuePatchInstalled = true

local states = setmetatable({}, { __mode = "k" })
local syncCache = setmetatable({}, { __mode = "k" })
local activeContext

local function optionInt(name, default, minimum, maximum)
    local options = SandboxVars and SandboxVars.ApocalypseBRFarmingSystem
    local value = math.floor(tonumber(options and options[name]) or default)
    return math.max(minimum, math.min(maximum, value))
end

local function stateFor(system)
    local q = states[system]
    if not q then
        q = Queue.new(getTimestampMs)
        states[system] = q
        -- One initialization scan, never a ten-minute snapshot or per-tick rebuild.
        for i = 1, system:getLuaObjectCount() do
            Queue.add(q, system:getLuaObjectByIndex(i))
        end
    end
    return q
end

local originalNew = SFarmingSystem.newLuaObject
function SFarmingSystem:newLuaObject(globalObject)
    local plant = originalNew(self, globalObject)
    Queue.add(stateFor(self), plant)
    return plant
end

local originalRemove = SFarmingSystem.removeLuaObject
function SFarmingSystem:removeLuaObject(plant)
    local valid = plant and plant.luaSystem == self
    local result = originalRemove(self, plant)
    if valid then
        Queue.remove(stateFor(self), plant)
        syncCache[plant] = nil
    end
    return result
end

local function captureContext(system)
    return { hoursElapsed = system.hoursElapsed,
        seasons = getSandboxOptions():getOptionByName("PlantGrowingSeasons"):getValue() == true,
        noInside = getSandboxOptions():getOptionByName("KillInsideCrops"):getValue() == true,
        seasonName = getClimateManager():getSeasonName(),
        temperature = season.currentTemp, weather = getWorld():getWeather(),
        sunny = season.weather == "sunny", raining = RainManager.isRaining(),
        precipitation = getClimateManager():getPrecipitationIntensity() }
end

local originalCheckWater = SFarmingSystem.checkWater
function SFarmingSystem:checkWater(plant)
    local context = activeContext
    if not context then return originalCheckWater(self, plant) end
    local factor = plant.hasWeeds and 2 or 1
    if context.raining and plant.exterior then
        plant.waterLvl = plant.waterLvl + 30 * context.precipitation / factor
        plant.lastWaterHour = context.hoursElapsed
    elseif context.sunny then
        plant.waterLvl = plant.waterLvl - 0.1 * factor
    end
    plant.waterLvl = math.max(0, math.min(100, plant.waterLvl))
end

-- Preserve all toModData extensions. Nested/custom mutable values conservatively
-- force transmission; scalar equality also checks keys removed since last send.
function SPlantGlobalObject:saveData()
    local start = getTimestampMs()
    local object = self:getIsoObject()
    if not object then return end
    local data = object:getModData()
    self:toModData(data)
    local cached = syncCache[self]
    local changed = not cached or cached.object ~= object
    if not changed then
        for key, value in pairs(data) do
            if type(value) == "table" or type(value) == "userdata" or cached.values[key] ~= value then
                changed = true
                break
            end
        end
        if not changed then
            for key in pairs(cached.values) do
                if data[key] == nil then changed = true break end
            end
        end
    end
    if changed then
        object:transmitModData()
        local values = {}
        for key, value in pairs(data) do values[key] = value end
        syncCache[self] = { object = object, values = values }
    end
    local q = stateFor(self.luaSystem)
    q.sync = q.sync or { sent = 0, skipped = 0, totalMs = 0 }
    if changed then q.sync.sent = q.sync.sent + 1 else q.sync.skipped = q.sync.skipped + 1 end
    q.sync.totalMs = q.sync.totalMs + getTimestampMs() - start
end

local function updatePlantHealth(system, luaObject, context)
    if not luaObject or not luaObject:isAlive() or luaObject.state == "plow" then return end

    local seasons = context.seasons
    local noInside = context.noInside
    local greenhouse = false
    local square = luaObject:getSquare()

    if square then
        luaObject.exterior = square:isOutside()
        luaObject.hasWeeds = system:hasWeeds(square)
        if luaObject:hasVisibleFlies() then square:setHasFlies(true) end
        local room = square:getRoom()
        if not luaObject.exterior and room and room:getRoomDef() then
            local roomDef = string.lower(room:getRoomDef():getName())
            if string.contains(roomDef, "greenhouse") then greenhouse = true end
        end
    end

    local badMultiplier = 1
    if seasons and luaObject.exterior and luaObject:isBadMonth() and not luaObject:isBadMonthHardy() then
        luaObject.cursed = true
    end
    if seasons and luaObject.exterior and context.seasonName == "Winter" and not luaObject:isColdHardy() then
        luaObject.cursed = true
    end
    if luaObject.cursed then badMultiplier = badMultiplier * 2 end
    if luaObject.hasWeeds then badMultiplier = badMultiplier * 2 end
    if luaObject.naturalLight then badMultiplier = badMultiplier / luaObject.naturalLight end

    local prop
    if luaObject.typeOfSeed then prop = farming_vegetableconf.props[luaObject.typeOfSeed] end
    local houseplant = prop and prop.isHouseplant
    local weather = context.weather

    if weather == "sunny" then
        if luaObject.exterior then
            if not luaObject:isBadMonth() then luaObject.health = luaObject.health + (1 / badMultiplier) end
        elseif houseplant or greenhouse or not noInside then
            luaObject.health = luaObject.health + (0.25 / badMultiplier)
        end
    end

    if context.temperature <= 10 and not luaObject:isColdHardy() then
        luaObject.health = luaObject.health - 0.5 * badMultiplier
    end

    local water = farming_vegetableconf.calcWater(luaObject.waterNeeded, luaObject.waterLvl)
    local waterMax = farming_vegetableconf.calcWater(luaObject.waterLvl, luaObject.waterNeededMax)
    if water >= 0 and waterMax >= 0 then
        luaObject.health = luaObject.health + 0.4 / badMultiplier
    elseif water == -1 then
        luaObject.health = luaObject.health - 0.2 * badMultiplier
    elseif water == -2 then
        luaObject.health = luaObject.health - 0.5 * badMultiplier
    elseif waterMax == -1 and luaObject.health > 20 then
        luaObject.health = luaObject.health - 0.2 * badMultiplier
    elseif waterMax == -2 and luaObject.health > 20 then
        luaObject.health = luaObject.health - 0.5 * badMultiplier
    end

    if noInside and (not luaObject.exterior) and (not houseplant) and (not greenhouse) then
        luaObject.health = luaObject.health - (1 * badMultiplier)
    end
    if seasons and luaObject.exterior and context.seasonName == "Winter" and houseplant then
        luaObject.health = luaObject.health - (1.5 * badMultiplier)
    end
    if seasons and luaObject.exterior and luaObject:isBadMonth() and not luaObject:isBadMonthHardy() then
        luaObject.health = luaObject.health - (3 * badMultiplier)
    end
end

local function dispatch(system, q, phase, plant, context)
    local oldHour, oldContext = system.hoursElapsed, activeContext
    system.hoursElapsed, activeContext = context.hoursElapsed, context
    -- A failed operation is retired, never retried after a partial mutation.
    -- Restore the public clock even if another mod's callback raises an error.
    local ok, message = pcall(function()
        if phase == "check" then system:checkPlant2(plant)
        elseif phase == "plowFade" then system:plowFadeCheck(plant)
        elseif phase == "waterDisease" and plant:isAlive() then
            plant:lowerWaterLvl()
            plant:upDisease()
        elseif phase == "health" then updatePlantHealth(system, plant, context) end
    end)
    system.hoursElapsed, activeContext = oldHour, oldContext
    if not ok then
        q.errors = (q.errors or 0) + 1
        if q.errors <= 3 then print("[" .. MODULE .. "] phase=" .. phase .. " error=" .. tostring(message)) end
    end
end

local function logBoundary(q, result)
    print("[" .. MODULE .. "] interval ticks=" .. result.ticks
        .. " quota=" .. result.oldQuota .. "->" .. result.quota
        .. " backlogOps=" .. result.backlog .. " jobs=" .. result.jobs
        .. " drainTick=" .. result.drainTick .. " timeCappedTicks=" .. result.cappedTicks
        .. " maxTickMs=" .. result.maxTickMs .. " processed=" .. result.processed
        .. " skippedRemoved=" .. result.skipped .. " errors=" .. (q.errors or 0))
    for _, phase in ipairs({ "waterDisease", "health", "check", "plowFade" }) do
        local stat = result.phases[phase]
        if stat then
            print("[" .. MODULE .. "] phase=" .. phase .. " calls=" .. stat.calls
                .. " totalMs=" .. stat.totalMs .. " maxOperationMs=" .. stat.maxMs)
        end
    end
    if q.sync then
        print("[" .. MODULE .. "] sync sent=" .. q.sync.sent .. " unchanged=" .. q.sync.skipped
            .. " totalMs=" .. q.sync.totalMs .. " (included in phase timings)")
    end
    q.sync, q.errors = nil, 0
    if result.backlog > 0 and result.cappedTicks > 0 then
        print("[" .. MODULE .. "] backlog persists under the time limit; increased quota may not provide enough capacity")
    end
end

function SFarmingSystem:EveryTenMinutes()
    local begin = getTimestampMs()
    local q = stateFor(self)
    local result = Queue.boundary(q, optionInt("AdjustmentStep", 5, 1, 1000),
        optionInt("MaxOperationsPerTick", 10000, 1, 100000))
    logBoundary(q, result)
    local waterDue, healthDue = false, false
    local currentHour = math.floor(getGameTime():getTimeOfDay())
    if currentHour ~= self.previousHour then
        self.hoursElapsed = self.hoursElapsed + 1
        self.previousHour = currentHour
        self.hourElapsedForWater = self.hourElapsedForWater + 1
        local hourForWater = 5
        if SandboxVars.PlantResilience == 1 then hourForWater = 12
        elseif SandboxVars.PlantResilience == 2 then hourForWater = 8
        elseif SandboxVars.PlantResilience == 4 then hourForWater = 3
        elseif SandboxVars.PlantResilience == 5 then hourForWater = 2 end
        if self.hourElapsedForWater >= hourForWater then
            waterDue = true
            self.hourElapsedForWater = 0
        end
        self.previousHourHealth = self.previousHourHealth + 1
        if self.previousHourHealth == 2 then
            healthDue = true
            self.previousHourHealth = 0
        end
        self:sendCommand("hoursElapsed", { hoursElapsed = self.hoursElapsed })
    end
    local context = captureContext(self)
    if waterDue then Queue.enqueue(q, "waterDisease", false, context) end
    if healthDue then Queue.enqueue(q, "health", false, context) end
    Queue.enqueue(q, "check", false, context)
    Queue.enqueue(q, "plowFade", true, context)
    print("[" .. MODULE .. "] enqueueMs=" .. (getTimestampMs() - begin)
        .. " plants=" .. q.count .. " queuedOps=" .. q.remaining)
end

Events.OnTick.Add(function()
    local system = SFarmingSystem.instance
    if not system then return end
    local q = stateFor(system)
    Queue.tick(q, optionInt("TimeBudgetMs", 2, 1, 50), function(phase, plant, context)
        dispatch(system, q, phase, plant, context)
    end)
end)
print("[" .. MODULE .. "] adaptive farming queue patch installed")
