"""Production Lua 5.1 tests. Requires Python package lupa; no game install changes."""
from pathlib import Path
import json
from lupa.lua51 import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / "apoclaipsebr-farming-system/common/media"
lua = LuaRuntime(unpack_returned_tuples=True)
lua.globals().Queue = lua.execute((MEDIA / "lua/server/Farming/ApocalypseBRFarmQueue.lua").read_text())
lua.execute(r'''
local time = 0
local function clock() return time end
local function plant(id) return { id = id } end
local function drain(q, callback)
    local limit = 0
    while q.remaining > 0 do
        Queue.tick(q, 100, callback)
        limit = limit + 1
        assert(limit < 10000, "queue failed to drain")
    end
    assert(q.head > q.tail)
end

-- Mid-cycle additions are excluded; removed/replaced objects cannot inherit work.
local q = Queue.new(clock)
local a,b,c,d,e = plant(1),plant(2),plant(3),plant(4),plant(5)
for _,p in ipairs({a,b,c,d}) do Queue.add(q,p) end
Queue.enqueue(q,"check",false,{})
Queue.enqueue(q,"fade",true,{})
q.quota = 1
local seen = {}
local function visit(phase,p) table.insert(seen,phase .. p.id) end
Queue.tick(q,100,visit)
Queue.remove(q,b)
Queue.remove(q,d)
Queue.add(q,e)
drain(q,visit)
assert(table.concat(seen,",") == "check1,check3,fade3,fade1")
assert(q.remaining == 0 and q.count == 3)

-- Same Lua table re-added is a new membership generation.
q = Queue.new(clock)
Queue.add(q,a); Queue.enqueue(q,"old",false,{})
Queue.remove(q,a); Queue.add(q,a)
seen = {}; drain(q,visit); assert(#seen == 0)

-- Every phase finishes before the following phase begins.
q = Queue.new(clock)
for _,p in ipairs({a,b,c}) do Queue.add(q,p) end
for _,phase in ipairs({"water","health","check"}) do Queue.enqueue(q,phase,false,{}) end
Queue.enqueue(q,"fade",true,{})
seen = {}; q.quota = 2; drain(q,visit)
assert(table.concat(seen,",") == "water1,water2,water3,health1,health2,health3,check1,check2,check3,fade3,fade2,fade1")

-- Time cap permits one indivisible slow operation but no additional operation.
q = Queue.new(clock)
for _,p in ipairs({a,b,c}) do Queue.add(q,p) end
Queue.enqueue(q,"slow",false,{})
Queue.tick(q,2,function() time = time + 3 end)
assert(q.processed == 1 and q.remaining == 2 and q.cappedTicks == 1 and q.maxTickMs == 3)
local result = Queue.boundary(q,5,100)
assert(result.quota == 25 and result.backlog == 2)
drain(q,function() end)
for i=1,100 do Queue.tick(q,2,function() end) end
result = Queue.boundary(q,5,100)
assert(result.quota == 20)

-- Feedback approaches the smallest allowance that drains across an interval.
q = Queue.new(clock)
for i=1,200 do Queue.add(q,plant(i)) end
for cycle=1,30 do
    Queue.boundary(q,1,1000)
    Queue.enqueue(q,"check",false,{})
    for tick=1,20 do Queue.tick(q,100,function() end) end
end
assert(q.quota >= 10 and q.quota <= 12 and q.remaining == 0)

-- Sustained 30k-object overload cannot create more than two sweeps.
q = Queue.new(clock)
for i=1,30012 do Queue.add(q,plant(i)) end
q.quota=1
for step=1,100 do
    Queue.coalesce(q,{step=step})
    Queue.tick(q,2,function(_,p,context,node) node.lastStep=context.step; time=time+2 end)
    assert(q.tail-q.head+1<=2 and q.remaining<=2*q.count)
end
q.quota=100000
drain(q,function(_,p,context,node) node.lastStep=context.step end)
assert(q.completedFloor==100)

-- A sliced catch-up keeps a fair continuation sweep until all time is consumed.
q=Queue.new(clock); Queue.add(q,a); Queue.coalesce(q,{step=100}); q.quota=1
for i=1,9 do
    Queue.tick(q,100,function(_,p,context,node) node.lastStep=math.min(context.step,node.lastStep+12) end)
end
assert(q.remaining==0 and q.completedFloor==100)

-- Random mutation stress: surviving original members receive exactly one visit
-- in each phase; additions and removed generations receive none thereafter.
math.randomseed(42)
for trial=1,500 do
    q = Queue.new(clock)
    local original = {}
    for i=1,30 do original[i]=plant(i); Queue.add(q,original[i]) end
    Queue.enqueue(q,"forward",false,{})
    Queue.enqueue(q,"reverse",true,{})
    local visits = {}
    q.quota = 1
    local ticks = 0
    while q.remaining > 0 do
        if math.random(3)==1 then Queue.remove(q,original[math.random(30)]) end
        if math.random(4)==1 then Queue.add(q,plant(100+ticks)) end
        Queue.tick(q,100,function(phase,p)
            assert(p.id <= 30 and q.nodes[p] and q.nodes[p].alive)
            local key=phase..p.id
            assert(not visits[key]); visits[key]=true
        end)
        ticks=ticks+1; assert(ticks<200)
    end
    for _,p in ipairs(original) do
        if q.nodes[p] then assert(visits["forward"..p.id] and visits["reverse"..p.id]) end
    end
    assert(q.remaining==0)
end
''')

# Load the actual monkey patches against deterministic game API fixtures.
lua.execute(r'''
time=0; hour=0; logs={}; tickCallback=nil
function isClient() return false end
function isServer() return serverMode == true end
function getTimestampMs() return time end
function print(message) table.insert(logs,message) end
Events={OnTick={Add=function(fn) tickCallback=fn end}}
SandboxVars={PlantResilience=5,ApocalypseBRFarmingSystem={TimeBudgetMs=2,AdjustmentStep=5}}
season={currentTemp=15,weather="cloudy"}
RainManager={isRaining=function() return false end}
function getWorld() return {getWeather=function() return "cloudy" end} end
function getClimateManager() return {getSeasonName=function() return "Summer" end,getPrecipitationIntensity=function() return 0 end} end
function getSandboxOptions() return {getOptionByName=function() return {getValue=function() return false end} end} end
function getGameTime() return {getTimeOfDay=function() return hour end, getWorldAgeHours=function() return hour end} end
SFarmingSystem={}
function SFarmingSystem:getLuaObjectCount() return #self.plants end
function SFarmingSystem:getLuaObjectByIndex(i) return self.plants[i] end
function SFarmingSystem:newLuaObject(p) p.luaSystem=self; self.plants[#self.plants+1]=p; return p end
function SFarmingSystem:removeLuaObject(p)
    if not p or p.luaSystem~=self then return end
    for i,v in ipairs(self.plants) do if v==p then table.remove(self.plants,i); break end end
end
function SFarmingSystem:getLuaObjectAt(x) return self.plants[x == 1 and 2 or 1] end
function SFarmingSystem:checkPlantSquare(p) end
function SFarmingSystem:growPlant(p) p.growth=(p.growth or 0)+1; p.nextGrowing=self.hoursElapsed+1 end
function SFarmingSystem:checkPlant2(p)
    self:checkWater(p)
    self.observedHour=self.hoursElapsed
    if p.fail then error("fixture error") end
    p:saveData()
end
function SFarmingSystem:checkWater(p) p.fallback=true end
function SFarmingSystem:plowFadeCheck(p) end
function SFarmingSystem:sendCommand() end
SPlantGlobalObject={}
function SPlantGlobalObject:toModData(data)
    data.health=self.health; data.waterLvl=self.waterLvl; data.custom=self.custom
end
function SPlantGlobalObject:getIsoObject() return self.object end
function SPlantGlobalObject:isAlive() return self.state=="seeded" or self.state=="plow" end
function SPlantGlobalObject:addIcon() end
function SPlantGlobalObject:setSpriteName() end
function SPlantGlobalObject:water(_,uses)
    self.waterLvl=math.min(100,self.waterLvl+10*uses)
    self:saveData()
end
function SPlantGlobalObject:destroyThis()
    if self.state=="plow" then self:removeObject() else self.state="destroyed" end
end
function SPlantGlobalObject:removeObject() self.luaSystem:removeLuaObject(self) end
function ZombRandFloat() return 0.5 end
function SPlantGlobalObject:checkStat()
    self.luaSystem.observedHour=self.luaSystem.hoursElapsed
    if self.fail then error("fixture error") end
end
function SPlantGlobalObject:getSquare() return nil end
function SPlantGlobalObject:hasVisibleFlies() return (self.fliesLvl or 0)>30 end
function SPlantGlobalObject:isBadMonth() return false end
function SPlantGlobalObject:isBadMonthHardy() return true end
function SPlantGlobalObject:isColdHardy() return true end
function SPlantGlobalObject:lowerWaterLvl() self.waterLvl=self.waterLvl-1 end
function SPlantGlobalObject:upDisease() end
farming_vegetableconf={props={},calcWater=function() return 0 end,getSpriteName=function() return nil end}
package.loaded["Farming/SFarmingSystem"]=true
package.loaded["Farming/SPlantGlobalObject"]=true
package.loaded["Farming/ApocalypseBRFarmQueue"]=Queue
function makeObject()
    return { data={},sends=0,getModData=function(self) return self.data end,
        transmitModData=function(self) self.sends=self.sends+1 end }
end
system=setmetatable({plants={},hoursElapsed=0,previousHour=0,hourElapsedForWater=0,previousHourHealth=0}, {__index=SFarmingSystem})
SFarmingSystem.instance=system
''')
main = (MEDIA / "lua/server/ApocalypseBR_FarmingSystem.lua").read_text()
lua.execute(main)
lua.execute(r'''
local p=setmetatable({health=50,waterLvl=50,state="seeded",object=makeObject()}, {__index=SPlantGlobalObject})
system:newLuaObject(p)
p:saveData(); p:saveData(); assert(p.object.sends==1)
p.object.data.health=999
p:saveData(); assert(p.object.sends==2 and p.object.data.health==50)
local oldObject=p.object; p.object=nil; p:saveData(); p.object=oldObject
p:saveData(); assert(p.object.sends==3)
p.health=51; p:saveData(); assert(p.object.sends==4)
p.custom="new"; p:saveData(); assert(p.object.sends==5)
p.custom=nil; p:saveData(); assert(p.object.sends==6)
p.custom={a=1}; p:saveData(); p.custom.a=2; p:saveData(); assert(p.object.sends==8)
p.custom=nil
p.object=makeObject(); p:saveData(); assert(p.object.sends==1)
system:EveryTenMinutes()
system:EveryTenMinutes()
system.hoursElapsed=9
RainManager.isRaining=function() return true end
getClimateManager=function() return {getSeasonName=function() return "Winter" end,getPrecipitationIntensity=function() return 1 end} end
tickCallback()
assert(system.observedHour==0 and system.hoursElapsed==9 and p.waterLvl==50)
RainManager.isRaining=function() return false end
-- Failed callback still restores the clock and does not poison subsequent ticks.
p.fail=true; system:EveryTenMinutes(); system:EveryTenMinutes(); tickCallback()
assert(system.hoursElapsed==9)
p.fail=false; system:checkWater(p); assert(p.fallback)
-- Hourly maintenance preserves vanilla cadence and health formula.
hour=1; system:EveryTenMinutes(); tickCallback()
hour=2; system:EveryTenMinutes(); tickCallback(); system:getLuaObjectAt(0,0,0)
assert(system.hoursElapsed==11 and p.health==51.4 and p.waterLvl==49)
system:removeLuaObject(p); system:newLuaObject(p)
p:saveData(); assert(p.object.sends>=2)
''')
# Chronological growth, weather, player edits, cleanup and sliced recovery.
lua.execute(r'''
local rain=false
RainManager.isRaining=function() return rain end
getClimateManager=function() return {getSeasonName=function() return "Summer" end,
    getPrecipitationIntensity=function() return 1 end} end
local function fresh()
    hour=0
    system=setmetatable({plants={},hoursElapsed=0,previousHour=0,hourElapsedForWater=0,
        previousHourHealth=0}, {__index=SFarmingSystem})
    SFarmingSystem.instance=system
    return system
end
local function crop()
    return setmetatable({health=50,waterLvl=50,state="seeded",object=makeObject()},
        {__index=SPlantGlobalObject})
end
fresh(); local p=crop(); p.exterior=true; p.nextGrowing=1; system:newLuaObject(p)
for i=0,3 do hour=i; rain=(i<2); system:EveryTenMinutes() end
local currentHour=system.hoursElapsed
system:getLuaObjectAt(0,0,0)
assert(p.growth==3 and system.hoursElapsed==currentHour and p.waterLvl==99)
-- Direct mutation catches up BEFORE applying new water; old drought cannot run after it.
season.weather="sunny"; rain=false
fresh(); p=crop(); system:newLuaObject(p)
for i=1,8 do system:EveryTenMinutes() end
p:water(nil,1); assert(math.abs(p.waterLvl-59.2)<1e-8)
tickCallback(); assert(math.abs(p.waterLvl-59.2)<1e-8)
-- New membership starts at the current target, not the beginning of history.
local late=crop(); system:newLuaObject(late)
late:water(nil,0); assert(late.waterLvl==50)
-- Long accumulated time is sliced and continuation sweeps finish without new requests.
fresh(); p=crop(); system:newLuaObject(p)
for i=1,100 do system:EveryTenMinutes() end
for i=1,100 do tickCallback() end
assert(math.abs(p.waterLvl-40)<1e-8)
-- Errors retire the failed interval and restore the public clock, then recover.
fresh(); p=crop(); p.fail=true; system:newLuaObject(p)
for i=1,4 do system:EveryTenMinutes() end
tickCallback(); p.fail=false
for i=1,10 do tickCallback() end
assert(math.abs(p.waterLvl-49.6)<1e-8 and system.hoursElapsed==0)
-- Empty watered furrows retain vanilla periodic drying before subsequent seeding.
fresh(); p=crop(); p.state="plow"; system:newLuaObject(p)
ZombRandFloat=function() return 0 end
hour=1; system:EveryTenMinutes(); hour=2; system:EveryTenMinutes()
system:getLuaObjectAt(0,0,0); assert(p.waterLvl==49 and #system.plants==1)
ZombRandFloat=function() return 0.5 end
-- Adaptive cadence obeys configured bounds and rounds upward to ten minutes.
SandboxVars.ApocalypseBRFarmingSystem.MinUpdateIntervalMinutes=35
SandboxVars.ApocalypseBRFarmingSystem.MaxUpdateIntervalMinutes=20
fresh(); p=crop(); system:newLuaObject(p); system:EveryTenMinutes()
local found=false
for _,line in ipairs(logs) do if string.find(line,"revisitMinutes=40",1,true) then found=true end end
assert(found)
SandboxVars.ApocalypseBRFarmingSystem.MinUpdateIntervalMinutes=nil
SandboxVars.ApocalypseBRFarmingSystem.MaxUpdateIntervalMinutes=nil
-- An old eligible cleanup plot uses one sampled deadline and no crop callbacks.
fresh(); local square={data={plowDay=-31},getModData=function(self) return self.data end}
p=crop(); p.state="harvested"; p.getSquare=function() return square end
system:newLuaObject(p)
ZombRandFloat=function() return 0 end
for i=1,2 do system:EveryTenMinutes() end
tickCallback(); assert(#system.plants==0)
ZombRandFloat=function() return 0.5 end
season.weather="cloudy"
''')

# Multiplayer saves coalesce across actions and simulation, using the tick budget.
vanilla_path = Path("Z:/SteamLibrary/steamapps/common/ProjectZomboid/media/lua/server/Farming/SFarmingSystem.lua")
commands_path = vanilla_path.with_name("farmingCommands.lua")
if commands_path.exists():
    lua.execute(commands_path.read_text(encoding="utf-8"))
lua.execute(r'''
serverMode=true
hour=0
system=setmetatable({plants={},hoursElapsed=0,previousHour=0,hourElapsedForWater=0,
    previousHourHealth=0}, {__index=SFarmingSystem})
SFarmingSystem.instance=system
local function crop()
    return system:newLuaObject(setmetatable({health=50,waterLvl=10,state="seeded",
        object=makeObject()}, {__index=SPlantGlobalObject}))
end
local p, other=crop(), crop()
season.weather="sunny"
system:EveryTenMinutes()
system:EveryTenMinutes()
for i=1,4 do
    if SFarmingSystemCommands then
        SFarmingSystemCommands.water(nil, {x=0,y=0,z=0,uses=1})
    else
        system:getLuaObjectAt(0,0,0):water(nil,1)
    end
end
assert(math.abs(p.waterLvl-49.8)<1e-8 and p.object.sends==0)
system:EveryTenMinutes()
system:EveryTenMinutes()
tickCallback()
assert(p.object.sends==1 and p.object.data.waterLvl==p.waterLvl)
assert(math.abs(p.waterLvl-49.6)<1e-8)
assert(other.object.sends==1 and other.object.data.waterLvl==other.waterLvl)
tickCallback()
assert(p.object.sends==1 and other.object.sends==1)
p:water(nil,1); tickCallback(); assert(p.object.sends==2)
p:saveData(); tickCallback(); assert(p.object.sends==2)

-- Old membership cannot publish after a plant is removed and re-registered.
other:water(nil,1)
system:removeLuaObject(other)
system:newLuaObject(other)
tickCallback(); assert(other.object.sends==1)

-- A slow send uses the tick allowance. The oldest remaining plant is sent
-- next tick, with all writes made while it was waiting included.
function p.object:transmitModData()
    self.sends=self.sends+1
    time=time+2
end
p:water(nil,1); other:water(nil,1)
tickCallback(); assert(p.object.sends==3 and other.object.sends==1)
other:water(nil,1)
tickCallback()
assert(other.object.sends==2 and other.object.data.waterLvl==other.waterLvl)

-- A failed publication cannot prevent a different plant from syncing.
function p.object:transmitModData() error("send failed") end
p:water(nil,1); other:water(nil,1)
tickCallback(); assert(other.object.sends==3)
serverMode=false
season.weather="cloudy"
''')

# Different commands on one plant share a save, and another plant syncs independently.
lua.execute(r'''
serverMode=true
system=setmetatable({plants={},hoursElapsed=0}, {__index=SFarmingSystem})
SFarmingSystem.instance=system
local function crop()
    return system:newLuaObject(setmetatable({health=50,waterLvl=10,state="seeded",
        object=makeObject()}, {__index=SPlantGlobalObject}))
end
local p, other=crop(), crop()
local commands = SFarmingSystemCommands or {
    water=function(_, args)
        system:getLuaObjectAt(args.x):water(nil, args.uses)
    end,
    cheat=function(_, args)
        local plant = system:getLuaObjectAt(args.x)
        plant[args.var] = plant[args.var] + args.count
        plant:saveData()
    end,
}
commands.testCustomPlantCommand = function(_, args)
    local plant = system:getLuaObjectAt(args.x)
    assert(plant.waterLvl==20 and plant.health==55)
    plant.custom="treated"
    plant:saveData()
end
commands.water(nil, {x=0,y=0,z=0,uses=1})
commands.cheat(nil, {x=0,y=0,z=0,var="health",count=5})
commands.testCustomPlantCommand(nil, {x=0})
commands.water(nil, {x=1,y=0,z=0,uses=2})
assert(p.object.sends==0 and other.object.sends==0)
tickCallback()
assert(p.object.sends==1 and other.object.sends==1)
assert(p.object.data.health==55 and p.object.data.waterLvl==20 and p.object.data.custom=="treated")
assert(other.object.data.health==50 and other.object.data.waterLvl==30 and other.object.data.custom==nil)
tickCallback()
assert(p.object.sends==1 and other.object.sends==1)
commands.testCustomPlantCommand=nil
serverMode=false
''')

# Reload guard must leave the existing event callback in place.
lua.execute("originalTickCallback = tickCallback")
lua.execute(main)
assert lua.eval("tickCallback == originalTickCallback")

# Compare health results to the actual installed 42.21 function, including season,
# greenhouse, weed, houseplant and water branches. No install files are changed.
if vanilla_path.exists():
    source = vanilla_path.read_text(encoding="utf-8")
    health = source[source.index("function SFarmingSystem:changeHealth()"):source.index("function SFarmingSystem:checkPlant()")]
    lua.execute("VanillaFarming = {}")
    lua.execute(health.replace("function SFarmingSystem:changeHealth()", "function VanillaFarming:changeHealth()", 1))
    lua.execute(r'''
    local config
    string.contains=function(value,part) return string.find(value,part,1,true)~=nil end
    function getWorld() return {getWeather=function() return config.weather end} end
    function getClimateManager() return {getSeasonName=function() return config.seasonName end,getPrecipitationIntensity=function() return 0 end} end
    function getSandboxOptions() return {getOptionByName=function(_,name)
        return {getValue=function() if name=="PlantGrowingSeasons" then return config.seasons else return config.noInside end end}
    end} end
    function SFarmingSystem:hasWeeds() return config.weeds end
    function SPlantGlobalObject:isBadMonth() return config.badMonth end
    function SPlantGlobalObject:isBadMonthHardy() return config.monthHardy end
    function SPlantGlobalObject:isColdHardy() return config.coldHardy end
    function SPlantGlobalObject:hasVisibleFlies() return config.flies end
    function SPlantGlobalObject:getSquare()
        return {isOutside=function() return config.outside end,setHasFlies=function() end,
            getRoom=function() return {getRoomDef=function() return {getName=function()
                return config.greenhouse and "GreenhouseRoom" or "Kitchen"
            end} end} end}
    end
    farming_vegetableconf.calcWater=function(first)
        if first==17 then return config.water else return config.waterMax end
    end
    math.randomseed(73)
    for trial=1,250 do
        local function coin() return math.random(2)==1 end
        config={weather=coin() and "sunny" or "cloudy", seasonName=coin() and "Winter" or "Summer",
            seasons=coin(),noInside=coin(),weeds=coin(),badMonth=coin(),monthHardy=coin(),
            coldHardy=coin(),outside=coin(),greenhouse=coin(),flies=coin(),
            water=math.random(3)-3,waterMax=math.random(3)-3}
        season.currentTemp=coin() and 5 or 20
        season.weather=config.weather
        farming_vegetableconf.props.Test={isHouseplant=coin()}
        local function newPlant()
            return setmetatable({health=50,waterLvl=50,waterNeeded=17,waterNeededMax=80,
                typeOfSeed="Test",naturalLight=0.5,state="seeded",object=makeObject()},
                {__index=SPlantGlobalObject})
        end
        local expected,actual=newPlant(),newPlant()
        local reference=setmetatable({plants={expected}}, {__index=SFarmingSystem})
        VanillaFarming.changeHealth(reference)
        system=setmetatable({plants={},hoursElapsed=0,previousHour=0,
            hourElapsedForWater=0,previousHourHealth=1}, {__index=SFarmingSystem})
        SFarmingSystem.instance=system
        system:newLuaObject(actual)
        hour=1
        system:EveryTenMinutes()
        system:EveryTenMinutes()
        tickCallback()
        assert(math.abs(actual.health-expected.health)<0.00000001, "vanilla health mismatch")
    end
    ''')
    print("PASS: 250 health scenarios compared with installed vanilla")
    plant_source = vanilla_path.with_name("SPlantGlobalObject.lua").read_text(encoding="utf-8")
    disease = plant_source[plant_source.index("function SPlantGlobalObject:upDisease()"):plant_source.index("-- lower by 1")]
    lua.execute("ReferencePlant = {}")
    lua.execute(disease.replace("function SPlantGlobalObject:upDisease()", "function ReferencePlant:upDisease()", 1))
    lua.execute(r'''
    -- Catch-up must use recorded winter weather even if the live season changed.
    local winter=false
    getClimateManager=function() return {getSeasonName=function() return winter and "Winter" or "Summer" end,
        getPrecipitationIntensity=function() return 0 end} end
    getSandboxOptions=function() return {getOptionByName=function() return {getValue=function() return false end} end} end
    getWorld=function() return {getWeather=function() return "cloudy" end} end
    season.currentTemp=20; season.weather="cloudy"
    RainManager.isRaining=function() return false end
    function SFarmingSystem:checkPlantSquare(p) end
    function SPlantGlobalObject:getSquare() return nil end
    function SPlantGlobalObject:isBadMonth() return false end
    function SPlantGlobalObject:isBadMonthHardy() return true end
    function SPlantGlobalObject:isColdHardy() return true end
    function SPlantGlobalObject:hasVisibleFlies() return self.fliesLvl>30 end
    math.randomseed(19)
    for trial=1,200 do
        local water=math.random(3)-3
        farming_vegetableconf.calcWater=function() return water end
        local function crop()
            return setmetatable({health=50,waterLvl=50,state="seeded",object=makeObject(),
                exterior=math.random(2)==1,mildewLvl=math.random(0,100),fliesLvl=math.random(0,100),
                slugsLvl=math.random(0,100),aphidLvl=math.random(0,100)}, {__index=SPlantGlobalObject})
        end
        local actual=crop(); local expected={}
        for k,v in pairs(actual) do expected[k]=v end
        setmetatable(expected,{__index=SPlantGlobalObject})
        system=setmetatable({plants={},hoursElapsed=0,previousHour=0,hourElapsedForWater=1,
            previousHourHealth=0}, {__index=SFarmingSystem})
        SFarmingSystem.instance=system; system:newLuaObject(actual)
        hour=1; winter=math.random(2)==1
        ReferencePlant.upDisease(expected)
        system:EveryTenMinutes()
        winter=not winter
        system:getLuaObjectAt(0,0,0)
        for _,field in ipairs({"mildewLvl","fliesLvl","slugsLvl","aphidLvl"}) do
            assert(actual[field]==expected[field],"historical disease mismatch: "..field)
        end
    end
    ''')
    print("PASS: 200 disease scenarios compared with installed vanilla across delayed season changes")

else:
    print("SKIP: installed vanilla health comparison (game path unavailable)")
for path in [ROOT / "README.md", ROOT / "workshop/description.txt", Path(__file__)]:
    assert not path.read_bytes().startswith(bytes([239, 187, 191])), path
for path in MEDIA.rglob("*.json"):
    json.loads(path.read_text(encoding="utf-8"))
for path in MEDIA.rglob("*"):
    if path.is_file() and path.suffix in {".lua", ".json", ".txt"}:
        assert not path.read_bytes().startswith(bytes([239, 187, 191])), path
print("PASS: Lua 5.1, 30,012-object bounded overload, 500 mutation scenarios, sliced recovery, growth/weather/action ordering, cleanup, time caps, synchronization, JSON/encoding")
