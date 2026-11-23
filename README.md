# ApocalipseBR adaptive farming system

Server-side Lua farming override checked against the installed Build 42.21.0 farming code.

## Why the scheduler changed

The previous scheduler appended two whole-world passes every ten game minutes, plus periodic health and water/disease passes. With 30,012 registered farming objects, it created at least 60,024 operations per interval. A server processing about 30,515 operations per interval could never drain that workload by increasing the operation quota while every tick was already time-capped.

The new scheduler coalesces requests into at most **two sweeps**: one immutable current sweep and one replaceable pending sweep. Each plant carries its last simulated ten-minute interval. New requests advance the pending target instead of adding another historical scan. With stable membership, estimated queued visits stay at or below twice the registered object count; removed objects can temporarily inflate the estimate until a sweep retires them. The registered count includes empty furrows and dead/harvested plots, not just growing crops.

## Elapsed-time simulation

EveryTenMinutes retains vanilla hourly counters and captures one shared context containing the farming hour, rain intensity, sunny weather, temperature, season, world age and scheduled health/water events. OnTick catches plants up to a sweep's target in chronological order.

This is a hybrid elapsed-time implementation. It replays inexpensive interval calculations rather than multiplying every effect by a time delta. Water clamps, water-dependent health/disease rates, death checks and overdue growth stages therefore retain their intermediate thresholds. Growth uses the historical farming hour so a delayed stage does not reschedule its successor from the current hour. Growth deadlines are checked during catch-up; this release does not introduce a separate growth timing wheel.

A normal background visit handles at most twelve historical intervals and checks the tick deadline between them. If plants still owe simulation time after a sweep, a fair continuation sweep is scheduled immediately. One indivisible callback can still exceed the time budget.

Loaded-square inspection and the final icon/sprite refresh happen once per growing-plant visit. Health events retain the vanilla health calculation and their own square inspection. Catch-up collects its save at the end of the visit. Unchanged scalar IsoObject data still suppresses redundant transmission while preserving toModData extensions and detecting external edits/object replacement.

Inactive plots do not receive the growing-crop simulation path or a second whole-world cleanup pass. Empty watered furrows still receive vanilla periodic water/disease maintenance before subsequent seeding. Dead-plant destruction and eligible plot fading use runtime geometric deadlines instead of repeated independent rolls. The probability over N eligible intervals remains `1 - (1 - 1/denominator)^N`, using vanilla denominators 5000 and 20000. Plot fading remains limited to loaded squares and plots older than thirty world days. The RNG sequence and ordering across plants change; this is distributional equivalence, not identical random outcomes.

## Adaptation and settings

| Sandbox setting | Default | Behavior |
| --- | --- | --- |
| TimeBudgetMs | 2 | Wall-clock budget for the OnTick farming batch; not a hard cap on individual callbacks. |
| MinUpdateIntervalMinutes | 20 | Shortest background sweep request interval in game minutes. |
| MaxUpdateIntervalMinutes | 60 | Longest adaptive sweep request interval; never below the minimum. |
| AdjustmentStep | 5 | Operation-quota adjustment when throughput is quota-limited. |
| MaxOperationsPerTick | 10000 | Maximum tuned plant visits per tick, not a population limit. |

Intervals round up to multiples of ten game minutes. The allowance starts at 25. Unfinished work increases it only when the prior interval was not time-capped. Finishing with more than 10% of ticks to spare lowers it, down to one. When a sweep request is due, time-capped pending work lengthens the revisit interval by ten game minutes; an empty queue with more than 30% of ticks to spare shortens it. The configured interval range bounds request cadence, not actual simulation lag under overload.

Use game time for crop progression and wall time for CPU limits. Faster game-time progression naturally requires more catch-up. Increase TimeBudgetMs only after checking that the server has spare processing capacity. Raising MaxOperationsPerTick cannot overcome an already saturated time budget.

## Player actions and compatibility

Server getLuaObjectAt lookups catch a plant up before native farming commands and server timed actions evaluate it. Direct water, seed, fertilizer, compost, cure and harvestThis calls also catch up before mutation. Foreground catch-up is synchronous and can exceed the background budget when a very delayed plant is accessed. An object removed during catch-up is not returned by the lookup.

Multiplayer actions apply to the authoritative plant immediately, in their existing order. Every action that calls `SPlantGlobalObject:saveData()` shares one pending modData publication per plant with the simulation. A mix of watering, seeding, fertilizing, treating disease, harvesting or other saves on the same plant still uses one queue entry. The tick normally sends the latest state after the sweep, with at most one queued sync per plant. The sync queue shares `TimeBudgetMs` with the sweep. If the tick runs out of time, the remaining publications get first use of the next tick's budget. Single-player saves remain immediate outside catch-up.

The client farming information window still reads synchronized IsoObject data; opening it does not send a new immediate-refresh request. Its values may trail the background sweep until synchronization arrives.

A linked registry is seeded once and maintained through SFarmingSystem.newLuaObject/removeLuaObject. Membership generations prevent removed/recreated plants from inheriting old work. Mods bypassing those lifecycle methods must use them instead. Changes to registered plant state are recognized on the next visit. Mods replacing the patched methods after this mod can supersede these optimizations; custom checkPlant2 side effects are not replayed by this replacement. Earlier saveData side effects are also not reproduced, although toModData extensions are honored.

Weather history is shared and reclaimed only after a completed sweep proves every surviving member has consumed it. It is deliberately **not discarded or capped**: persistent extreme overload can still grow retained history and simulation lag, although pending plant sweeps remain bounded. No crop time is silently dropped or extrapolated from today's weather.

Historical contexts do not snapshot the entire world. Square availability, weeds, neighbor disease, month-dependent crop methods and disease discovery callbacks remain live. Winter pest progression uses the captured season. Cleanup observes square availability at visits rather than reconstructing its loading history. Catch-up preserves per-plant interval order, not the former global phase ordering across all plants.

The farming clock and active context are restored even when a callback fails. An interval is retired before its mutations and is not replayed after a partial failure; other plants continue. Fix logged errors before assessing simulation results. Queue links, cursors, sampled deadlines, synchronization caches and weather history are runtime-only and are not persisted across restart. Pending updates are lost on restart, as in the previous queue implementation.

## Logs and validation

Each boundary reports quota changes, estimated pending visits, job count, processed/skipped visits, time-capped ticks, maximum batch time and errors. Phase `update` measures background catch-up. On multiplayer servers, the sync line reports publication time separately, along with `coalescedSaves` for repeated saves absorbed by the queue and `syncBacklog` for pending publications at the boundary.

Additional fields:

- `revisitMinutes`: current background request interval.
- `oldestLagMinutes`: conservative lag bound from the slowest member of the last completed sweep. It can overstate current lag while another sweep progresses or objects are removed.
- `maxVisitLagMinutes`: maximum remaining time debt observed after visits since the prior boundary.
- `simulatedIntervals`: growing-crop intervals replayed, distinct from plant visits.
- `retainedWeatherIntervals`: shared historical contexts not yet reclaimed.

A bounded job count alone is not evidence of adequate capacity. Watch simulation lag and retained history: both should settle rather than grow continuously. A maximum revisit setting of sixty minutes is not a guarantee that every plant is current within an hour.

Run `python tools/test_farming_queue.py` with Python and lupa installed. Tests execute production Lua under Lua 5.1, cover sustained 30,012-object overload, 500 mutation scenarios, continuation sweeps, quota feedback, time caps, historical rain and growth, watering order, cleanup, error recovery and synchronization. They compare 250 health and 200 delayed-season disease scenarios against installed vanilla code, and validate translation JSON and BOM-free text encoding.

Dedicated-server Kahlua timings, large-world lag stability and packet savings require runtime validation after deployment. The Python fixture results do not establish a production plant capacity.
