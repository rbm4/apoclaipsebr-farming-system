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
assert(result.quota == 30 and result.backlog == 2)
drain(q,function() end)
for i=1,100 do Queue.tick(q,2,function() end) end
result = Queue.boundary(q,5,100)
assert(result.quota == 25)

-- Feedback approaches the smallest allowance that drains across an interval.
q = Queue.new(clock)
for i=1,200 do Queue.add(q,plant(i)) end
for cycle=1,30 do
    Queue.boundary(q,1,1000)
    Queue.enqueue(q,"check",false,{})
    for tick=1,20 do Queue.tick(q,100,function() end) end
end
assert(q.quota >= 10 and q.quota <= 12 and q.remaining == 0)

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
function getTimestampMs() return time end
function print(message) table.insert(logs,message) end
Events={OnTick={Add=function(fn) tickCallback=fn end}}
SandboxVars={PlantResilience=5,ApocalypseBRFarmingSystem={TimeBudgetMs=2,AdjustmentStep=5}}
season={currentTemp=15,weather="cloudy"}
RainManager={isRaining=function() return false end}
function getWorld() return {getWeather=function() return "cloudy" end} end
function getClimateManager() return {getSeasonName=function() return "Summer" end,getPrecipitationIntensity=function() return 0 end} end
function getSandboxOptions() return {getOptionByName=function() return {getValue=function() return false end} end} end
function getGameTime() return {getTimeOfDay=function() return hour end} end
SFarmingSystem={}
function SFarmingSystem:getLuaObjectCount() return #self.plants end
function SFarmingSystem:getLuaObjectByIndex(i) return self.plants[i] end
function SFarmingSystem:newLuaObject(p) p.luaSystem=self; self.plants[#self.plants+1]=p; return p end
function SFarmingSystem:removeLuaObject(p)
    if not p or p.luaSystem~=self then return end
    for i,v in ipairs(self.plants) do if v==p then table.remove(self.plants,i); break end end
end
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
function SPlantGlobalObject:isAlive() return true end
function SPlantGlobalObject:getSquare() return nil end
function SPlantGlobalObject:isBadMonth() return false end
function SPlantGlobalObject:isBadMonthHardy() return true end
function SPlantGlobalObject:isColdHardy() return true end
function SPlantGlobalObject:lowerWaterLvl() self.waterLvl=self.waterLvl-1 end
function SPlantGlobalObject:upDisease() end
farming_vegetableconf={props={},calcWater=function() return 0 end}
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
local p=setmetatable({health=50,waterLvl=50,object=makeObject()}, {__index=SPlantGlobalObject})
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
system.hoursElapsed=9
RainManager.isRaining=function() return true end
getClimateManager=function() return {getSeasonName=function() return "Winter" end,getPrecipitationIntensity=function() return 1 end} end
tickCallback()
assert(system.observedHour==0 and system.hoursElapsed==9 and p.waterLvl==50)
RainManager.isRaining=function() return false end
-- Failed callback still restores the clock and does not poison subsequent ticks.
p.fail=true; system:EveryTenMinutes(); tickCallback()
assert(system.hoursElapsed==9)
p.fail=false; system:checkWater(p); assert(p.fallback)
-- Hourly maintenance preserves vanilla cadence and health formula.
hour=1; system:EveryTenMinutes(); tickCallback()
hour=2; system:EveryTenMinutes(); tickCallback()
assert(system.hoursElapsed==11 and p.health==51.4 and p.waterLvl==49)
system:removeLuaObject(p); system:newLuaObject(p)
p:saveData(); assert(p.object.sends>=2)
''')
# Reload guard must leave the existing event callback in place.
lua.execute("originalTickCallback = tickCallback")
lua.execute(main)
assert lua.eval("tickCallback == originalTickCallback")

# Compare health results to the actual installed 42.21 function, including season,
# greenhouse, weed, houseplant and water branches. No install files are changed.
vanilla_path = Path("Z:/SteamLibrary/steamapps/common/ProjectZomboid/media/lua/server/Farming/SFarmingSystem.lua")
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
        tickCallback()
        assert(math.abs(actual.health-expected.health)<0.00000001, "vanilla health mismatch")
    end
    ''')
    print("PASS: 250 health scenarios compared with installed vanilla")
else:
    print("SKIP: installed vanilla health comparison (game path unavailable)")
for path in MEDIA.rglob("*.json"):
    json.loads(path.read_text(encoding="utf-8"))
for path in MEDIA.rglob("*"):
    if path.is_file() and path.suffix in {".lua", ".json", ".txt"}:
        assert not path.read_bytes().startswith(bytes([239, 187, 191])), path
print("PASS: Lua 5.1 queue, 500 mutation scenarios, adaptation, time caps, integration, synchronization, JSON/encoding")
