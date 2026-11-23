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
        q.history, q.step, q.revisit, q.lastSweep = {}, 0, 2, 0
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
        precipitation = getClimateManager():getPrecipitationIntensity(),
        worldAge = getGameTime():getWorldAgeHours() }
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

local originalUpDisease = SPlantGlobalObject.upDisease
function SPlantGlobalObject:upDisease()
    if not activeContext then return originalUpDisease(self) end
    local water = farming_vegetableconf.calcWater(self.waterNeeded, self.waterLvl)
    local winter = activeContext.seasonName == "Winter" and self.exterior
    if self.mildewLvl and self.mildewLvl > 0 then
        self.mildewLvl = math.min(100, self.mildewLvl + (water >= 0 and 0.5 or 1))
    end
    if self.fliesLvl and self.fliesLvl > 0 then
        local visible = self:hasVisibleFlies()
        self.fliesLvl = winter and math.max(0, self.fliesLvl - 2)
            or math.min(100, self.fliesLvl + (water >= 0 and 0.5 or 1))
        if winter and visible and not self:hasVisibleFlies() then
            local square = self:getSquare()
            if square then square:setHasFlies(false) end
        end
    end
    if self.slugsLvl and self.slugsLvl > 0 then
        self.slugsLvl = winter and math.max(0, self.slugsLvl - 2)
            or math.min(100, self.slugsLvl + (water >= 0 and 0.5 or 1))
    end
    if self.aphidLvl and self.aphidLvl > 0 then
        if water == -1 or water == -2 or winter then
            self.aphidLvl = math.max(0, self.aphidLvl - 2)
        else
            self.aphidLvl = math.min(100, self.aphidLvl + 1)
        end
    end
end

-- Preserve all toModData extensions. Nested/custom mutable values conservatively
-- force transmission; scalar equality also checks keys removed since last send.
local function sameData(values, data)
    for key, value in pairs(data) do
        if type(value) == "table" or type(value) == "userdata" or values[key] ~= value then
            return false
        end
    end
    for key in pairs(values) do
        if data[key] == nil then return false end
    end
    return true
end

local function publishPlant(q, plant)
    local start = getTimestampMs()
    local object = plant:getIsoObject()
    if not object then syncCache[plant] = nil return end
    local data = object:getModData()
    local cached = syncCache[plant]
    local changed = not cached or cached.object ~= object
    -- Another mod may have changed/transmitted the IsoObject since our last send.
    -- Detect that before toModData overwrites fields back to the plant's values.
    if not changed then changed = not sameData(cached.values, data) end
    plant:toModData(data)
    if not changed then changed = not sameData(cached.values, data) end
    if changed then
        object:transmitModData()
        local values = {}
        for key, value in pairs(data) do values[key] = value end
        syncCache[plant] = { object = object, values = values }
    end
    q.sync = q.sync or { sent = 0, skipped = 0, totalMs = 0 }
    if changed then q.sync.sent = q.sync.sent + 1 else q.sync.skipped = q.sync.skipped + 1 end
    q.sync.totalMs = q.sync.totalMs + getTimestampMs() - start
end

-- All farming commands and simulation saves converge here. Queue by
-- registered plant identity, independent of the command that changed it.
function SPlantGlobalObject:saveData()
    if activeContext then return end
    local q = stateFor(self.luaSystem)
    if not isServer() then return publishPlant(q, self) end
    local node = q.nodes[self]
    if not node then return end
    if node.syncPending then
        q.coalescedSaves = (q.coalescedSaves or 0) + 1
        return
    end
    node.syncPending = true
    if q.lastSync then q.lastSync.nextSync = node else q.firstSync = node end
    q.lastSync = node
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

local function reportError(q, message)
    q.errors = (q.errors or 0) + 1
    if q.errors <= 3 then print("[" .. MODULE .. "] farming error=" .. tostring(message)) end
end

local function flushPlantSyncs(q)
    local last = q.lastSync
    while q.firstSync and q.clock() < q.deadline do
        local node = q.firstSync
        q.firstSync = node.nextSync
        if not q.firstSync then q.lastSync = nil end
        -- Keep this node marked while publishing, including during mod callbacks.
        if q.nodes[node.plant] == node then
            local ok, message = pcall(publishPlant, q, node.plant)
            if not ok then reportError(q, message) end
        end
        node.syncPending, node.nextSync = nil, nil
        if node == last then break end
    end
end

-- Geometric waiting time replaces repeated independent vanilla cleanup rolls.
-- RNG sequence changes, but the probability over N eligible intervals is the
-- same: 1 - (1 - 1/denominator)^N. Deadlines are runtime-only.
local function waitingTime(denominator)
    local u = math.max(0, math.min(0.999999999999, ZombRandFloat(0, 1)))
    return math.floor(math.log(1 - u) / math.log(1 - 1 / denominator)) + 1
end

local function inactiveUpdate(system, q, plant, node, target)
    if node.cleanupState ~= plant.state then
        node.cleanupState, node.destroyAt, node.fadeAt = plant.state, nil, nil
    end
    local first = node.lastStep + 1
    if not plant:isAlive() and plant.state ~= "destroyed" and plant.state ~= "harvested" then
        node.destroyAt = node.destroyAt or (node.lastStep + waitingTime(5000))
    end
    local square = plant:getSquare()
    if square and (plant.state == "plow" or plant.state == "destroyed" or plant.state == "harvested") then
        local data = square:getModData()
        if not data.plowDay then data.plowDay = q.history[first].worldAge / 24 end
        if not node.fadeAt then
            for step = first, target do
                if q.history[step].worldAge / 24 > data.plowDay + 30 then
                    node.fadeAt = step - 1 + waitingTime(20000)
                    break
                end
            end
        end
    elseif not square then
        -- Vanilla does not make fade rolls while the square is unavailable.
        node.fadeAt = nil
    end
    local destroy = node.destroyAt and node.destroyAt <= target
    local fade = node.fadeAt and node.fadeAt <= target
    if plant.state == "plow" then
        -- Vanilla considers empty furrows alive for hourly water/disease work.
        -- Watered soil must still dry before it is later seeded.
        local stop = math.min(target, node.destroyAt or target, node.fadeAt or target)
        for step = first, stop do
            local context = q.history[step]
            if context.waterDue then
                system.hoursElapsed, activeContext = context.hoursElapsed, context
                node.lastStep, node.simulationDirty = step, true
                plant:lowerWaterLvl()
                plant:upDisease()
            end
        end
    end
    if fade and (not destroy or node.fadeAt <= node.destroyAt) then
        node.lastStep, node.fadeAt = target, nil
        plant:removeObject()
    elseif destroy then
        node.lastStep = node.destroyAt
        node.destroyAt = nil
        plant:destroyThis()
        if q.nodes[plant] == node and node.lastStep < target then
            inactiveUpdate(system, q, plant, node, target)
        end
    end
    node.lastStep = target
end

local function simulateStep(system, plant, context)
    if context.waterDue and plant:isAlive() then
        plant:lowerWaterLvl()
        plant:upDisease()
    end
    if context.healthDue then updatePlantHealth(system, plant, context) end
    if plant.state ~= "plow" and plant:isAlive() then
        if plant.nextGrowing and system.hoursElapsed >= plant.nextGrowing then
            system:growPlant(plant, nil, true)
        end
        system:checkWater(plant)
        plant:checkStat()
    end
end

local function catchUp(system, q, plant, node, target, foreground)
    if activeContext or node.lastStep >= target then return end
    local oldHour, oldContext = system.hoursElapsed, activeContext
    local initialState = plant.state
    node.simulationDirty = nil
    local done = 0
    local ok, message = pcall(function()
        activeContext = q.history[node.lastStep + 1]
        -- Refresh loaded-world properties once, not once per missed interval.
        if plant.state ~= "plow" and plant:isAlive() then system:checkPlantSquare(plant) end
        while node.lastStep < target and q.nodes[plant] == node do
            if not foreground and done > 0 and
                (done >= 12 or (q.deadline and getTimestampMs() >= q.deadline)) then break end
            if plant.state == "plow" or not plant:isAlive() then
                inactiveUpdate(system, q, plant, node, target)
                break
            end
            local step = node.lastStep + 1
            local context = assert(q.history[step], "missing farming history")
            system.hoursElapsed, activeContext = context.hoursElapsed, context
            -- Retire an interval before callbacks: a partial failure must never
            -- replay water/growth already applied. The next visit continues.
            node.lastStep = step
            simulateStep(system, plant, context)
            done = done + 1
            q.simulated = (q.simulated or 0) + 1
        end
        if q.nodes[plant] == node and plant.state ~= "plow" then
            plant:addIcon()
            local sprite = farming_vegetableconf.getSpriteName(plant)
            if sprite then plant:setSpriteName(sprite) end
        end
    end)
    system.hoursElapsed, activeContext = oldHour, oldContext
    if not ok then reportError(q, message) end
    if q.nodes[plant] == node and
        (initialState ~= "plow" and initialState ~= "destroyed" and initialState ~= "harvested"
            or plant.state ~= initialState or node.simulationDirty) then
        local saved, errorMessage = pcall(plant.saveData, plant)
        if not saved then reportError(q, errorMessage) end
    end
    q.maxLag = math.max(q.maxLag or 0, q.step - node.lastStep)
end

-- Commands and server timed actions resolve plants through these native lookup
-- methods before testing harvest/water/seed preconditions. Foreground catch-up
-- is deliberately synchronous so old weather never overwrites a player edit.
local originalLookup = SFarmingSystem.getLuaObjectAt
function SFarmingSystem:getLuaObjectAt(x, y, z)
    local plant = originalLookup(self, x, y, z)
    if plant and not activeContext then
        local q = stateFor(self)
        local node = q.nodes[plant]
        if node then catchUp(self, q, plant, node, q.step, true) end
        if not q.nodes[plant] then return nil end
    end
    return plant
end

for _, name in ipairs({ "water", "seed", "fertilize", "fertilize2", "compostPlant",
    "cureMildew", "cureFlies", "cureAphids", "cureSlugs", "harvestThis" }) do
    local original = SPlantGlobalObject[name]
    if original then
        SPlantGlobalObject[name] = function(self, ...)
            if not activeContext then
                local q = stateFor(self.luaSystem)
                local node = q.nodes[self]
                if node then catchUp(self.luaSystem, q, self, node, q.step, true) end
                if not q.nodes[self] then return end
            end
            return original(self, ...)
        end
    end
end

local function logBoundary(q, result)
    print("[" .. MODULE .. "] interval ticks=" .. result.ticks
        .. " quota=" .. result.oldQuota .. "->" .. result.quota
        .. " backlogOps=" .. result.backlog .. " jobs=" .. result.jobs
        .. " drainTick=" .. result.drainTick .. " timeCappedTicks=" .. result.cappedTicks
        .. " maxTickMs=" .. result.maxTickMs .. " processed=" .. result.processed
        .. " skippedRemoved=" .. result.skipped .. " errors=" .. (q.errors or 0))
    print("[" .. MODULE .. "] revisitMinutes=" .. (q.revisit * 10)
        .. " oldestLagMinutes=" .. ((q.step - (q.completedFloor or 0)) * 10)
        .. " maxVisitLagMinutes=" .. ((q.maxLag or 0) * 10)
        .. " simulatedIntervals=" .. (q.simulated or 0)
        .. " retainedWeatherIntervals=" .. (q.step - (q.pruned or 0)))
    q.maxLag, q.simulated = 0, 0
    for _, phase in ipairs({ "update" }) do
        local stat = result.phases[phase]
        if stat then
            print("[" .. MODULE .. "] phase=" .. phase .. " calls=" .. stat.calls
                .. " totalMs=" .. stat.totalMs .. " maxOperationMs=" .. stat.maxMs)
        end
    end
    if q.sync or q.coalescedSaves or q.firstSync then
        local sync = q.sync or { sent = 0, skipped = 0, totalMs = 0 }
        print("[" .. MODULE .. "] sync sent=" .. sync.sent .. " unchanged=" .. sync.skipped
            .. " totalMs=" .. sync.totalMs .. " coalescedSaves=" .. (q.coalescedSaves or 0)
            .. " syncBacklog=" .. tostring(q.firstSync ~= nil))
    end
    q.sync, q.errors, q.coalescedSaves = nil, 0, nil
    if result.backlog > 0 and result.cappedTicks > 0 then
        print("[" .. MODULE .. "] time budget saturated; adapting sweep cadence, inspect simulation lag")
    end
end

function SFarmingSystem:EveryTenMinutes()
    local begin = getTimestampMs()
    local q = stateFor(self)
    local result = Queue.boundary(q, optionInt("AdjustmentStep", 5, 1, 1000),
        optionInt("MaxOperationsPerTick", 10000, 1, 100000))
    local minimum = math.ceil(optionInt("MinUpdateIntervalMinutes", 20, 10, 1440) / 10)
    local maximum = math.max(minimum,
        math.ceil(optionInt("MaxUpdateIntervalMinutes", 60, 10, 1440) / 10))
    q.revisit = math.max(minimum, math.min(maximum, q.revisit))
    if q.step - q.lastSweep >= q.revisit then
        if result.backlog > 0 and result.cappedTicks > 0 then
            q.revisit = math.min(maximum, q.revisit + 1)
        elseif result.backlog == 0 and result.ticks > 0 and result.drainTick < result.ticks * 0.7 then
            q.revisit = math.max(minimum, q.revisit - 1)
        end
    end
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
    q.step = q.step + 1
    q.targetStep = q.step
    context.step, context.waterDue, context.healthDue = q.step, waterDue, healthDue
    q.history[q.step] = context
    if q.step - q.lastSweep >= q.revisit then
        Queue.coalesce(q, context)
        q.lastSweep = q.step
    end
    -- Reclaim only history known to have been consumed by every survivor in a
    -- completed sweep. Never substitute today's weather for a delayed plant.
    local floor = q.count == 0 and q.step or (q.completedFloor or 0)
    for step = (q.pruned or 0) + 1, floor do q.history[step] = nil end
    q.pruned = floor
    print("[" .. MODULE .. "] enqueueMs=" .. (getTimestampMs() - begin)
        .. " plants=" .. q.count .. " queuedOps=" .. q.remaining)
end

Events.OnTick.Add(function()
    local system = SFarmingSystem.instance
    if not system then return end
    local q = stateFor(system)
    Queue.tick(q, optionInt("TimeBudgetMs", 2, 1, 50), function(phase, plant, context, node)
        catchUp(system, q, plant, node, context.step, false)
    end, flushPlantSyncs, q.syncBacklog)
    q.syncBacklog = q.firstSync ~= nil
end)
print("[" .. MODULE .. "] adaptive farming queue patch installed")
