# ApocalipseBR adaptive farming system

Server-side Lua override mod verified against the installed Build 42.20.0 and 42.21.0 farming code.
No game files or Java patches are needed.

EveryTenMinutes retains vanilla hourly counters and creates constant-time jobs.
OnTick drains water/disease, health, plant checks, then reverse plot cleanup in
FIFO order, including across overdue cycles. Each job captures membership at
enqueue using stable references and a creation serial cutoff. Removed/recreated
plants do not inherit old work. A linked registry is seeded once and maintained
through SFarmingSystem.newLuaObject/removeLuaObject; code bypassing those Lua
lifecycle methods must use them instead. Jobs and synchronization caches are
runtime-only, not persisted in plant/system modData.

## Adaptation and settings

The operation allowance starts at 25. At every in-game ten-minute boundary:

- Pending work raises the allowance by AdjustmentStep (default 5).
- A queue that finished with more than 10% of observed ticks to spare lowers it
  by that step, down to 1. Otherwise the allowance stays unchanged.
- MaxOperationsPerTick (default 10000) is a safety ceiling, not a plant target.
- TimeBudgetMs (default 2) stops starting more work after the elapsed-time limit,
  independently of the operation allowance.

The controller probes around the required throughput in fixed steps, rather than
guaranteeing an exact optimum. The tick-count feedback follows actual server
cadence and time acceleration. One plant operation cannot be interrupted and can
exceed the time limit. A wall-clock millisecond timer is used, matching the game's
exposed getTimestampMs API. Raising the allowance cannot overcome a saturated
time budget. In that case the logs flag persistent backlog; no scheduled cycles
are silently dropped or merged.

This system was design to favor server-side performance and 

## Simulation and synchronization

Jobs capture hoursElapsed, health weather/temperature/season and rain inputs.
During a synchronous operation the original public hour counter is restored even
when a callback fails. Vanilla growth scheduling sees that job's hour.
Square state, neighbor disease, player edits and month-dependent plant methods
remain live; asynchronous scheduling is not a snapshot of the whole world.
Plot fade continues to use vanilla's live world-age and RNG checks.

The health calculation follows the installed vanilla implementation. Vanilla
checkPlant2, lowerWaterLvl, upDisease and plowFadeCheck are reused. Non-queued
checkWater calls retain the original implementation.

saveData always calls toModData on the actual IsoObject. It suppresses only
unchanged scalar data for the same object; additions, deletions, external changes,
and object replacement force transmission. Nested tables/userdata conservatively
force transmission. An unavailable or removed plant clears its synchronization
cache. Mods overriding saveData after this mod can replace its optimization;
custom side effects in an earlier saveData override are not reproduced by this
replacement. toModData extensions are honored.

Errors are logged and a partially applied operation is retired, not replayed.
Other plants continue. Fix any reported phase errors before evaluating results.
Pending jobs are not persisted across restart, as with the previous mod.

## Measurement and validation

Each ten-minute boundary reports allowance changes, pending estimated operations,
jobs, processed/skipped counts, drain tick, time-capped ticks and maximum batch
duration. Removed nodes can be bypassed, so pending work is an upper estimate until
the affected phase completes. Each phase reports calls, total milliseconds and
maximum operation time. Sync reports sent/unchanged counts and its own time,
which is already included in phase costs. enqueueMs includes interval logging.
Timings have millisecond resolution. These logs are independent of Java telemetry;
farming work moves from EveryTenMinutes to OnTick.

Run python tools/test_farming_queue.py with Python and lupa installed. Tests execute
the production Lua under Lua 5.1 and cover mutation, ordering, tuning, time caps,
integration and synchronization. Dedicated-server behavior and packet savings
still need runtime validation after deploying the updated mod.
