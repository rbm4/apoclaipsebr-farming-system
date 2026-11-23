-- Runtime-only state: never store queue links in persisted farming modData.
local Queue = {}

function Queue.new(clock)
    return { clock = clock, nodes = {}, count = 0, serial = 0,
        jobs = {}, head = 1, tail = 0, remaining = 0, quota = 25,
        ticks = 0, intervalTicks = 0, drainTick = 0, phases = {},
        processed = 0, skipped = 0, cappedTicks = 0, maxTickMs = 0 }
end

function Queue.add(q, plant)
    if q.nodes[plant] then return end
    q.serial = q.serial + 1
    local node = { plant = plant, alive = true, serial = q.serial, prev = q.last,
        lastStep = q.targetStep or 0 }
    if q.last then q.last.next = node else q.first = node end
    q.last = node
    q.nodes[plant] = node
    q.count = q.count + 1
end

function Queue.remove(q, plant)
    local node = q.nodes[plant]
    if not node then return end
    node.alive = false
    -- Keep this node's links intact for jobs already pointing at it.
    if node.prev then node.prev.next = node.next else q.first = node.next end
    if node.next then node.next.prev = node.prev else q.last = node.prev end
    q.nodes[plant] = nil
    q.count = q.count - 1
end

function Queue.enqueue(q, phase, reverse, context)
    if q.count == 0 then return end
    q.tail = q.tail + 1
    q.jobs[q.tail] = { phase = phase, reverse = reverse,
        node = reverse and q.last or q.first, cutoff = q.serial,
        remaining = q.count, context = context }
    q.remaining = q.remaining + q.count
end

-- One immutable current sweep and one replaceable pending sweep. New requests
-- advance the pending target rather than adding historical plant scans.
function Queue.coalesce(q, context)
    q.targetStep = context.step
    if q.count == 0 then q.completedFloor = context.step return end
    if q.tail > q.head then
        local pending = q.jobs[q.tail]
        q.remaining = q.remaining - pending.remaining + q.count
        pending.node, pending.cutoff, pending.remaining = q.first, q.serial, q.count
        pending.context, pending.minStep = context, context.step
    else
        Queue.enqueue(q, "update", false, context)
        q.jobs[q.tail].minStep = context.step
    end
end

function Queue.boundary(q, step, maximum)
    local old = q.quota
    if q.intervalTicks > 0 then
        if q.remaining > 0 and q.cappedTicks == 0 then
            q.quota = math.min(maximum, q.quota + step)
        elseif q.remaining == 0 and q.drainTick < q.intervalTicks * 0.9 then
            q.quota = math.max(1, q.quota - step)
        end
    end
    q.quota = math.min(maximum, q.quota)
    local result = { oldQuota = old, quota = q.quota, backlog = q.remaining,
        jobs = math.max(0, q.tail - q.head + 1), ticks = q.intervalTicks,
        drainTick = q.drainTick, cappedTicks = q.cappedTicks,
        maxTickMs = q.maxTickMs, processed = q.processed, skipped = q.skipped,
        phases = q.phases }
    q.intervalTicks, q.drainTick, q.cappedTicks, q.maxTickMs = 0, 0, 0, 0
    q.processed, q.skipped, q.phases = 0, 0, {}
    return result
end

local function finish(q, job)
    -- Removals can unlink unvisited nodes, so retire their estimated work too.
    q.remaining = q.remaining - job.remaining
    q.jobs[q.head] = nil
    if job.minStep then q.completedFloor = math.max(q.completedFloor or 0, job.minStep) end
    q.head = q.head + 1
    if job.minStep and q.head <= q.tail then
        -- Keep numeric indices bounded too, even when overload never drains.
        local pending = q.jobs[q.head]
        q.jobs = { pending }
        q.head, q.tail = 1, 1
    end
    if q.head > q.tail then
        q.head, q.tail = 1, 0
        q.drainTick = q.intervalTicks
        -- Budget-limited catch-up can leave time debt after every member was
        -- visited. Continue fairly without waiting for another game boundary.
        if job.minStep and job.minStep < job.context.step and q.count > 0 then
            Queue.enqueue(q, "update", false, job.context)
            q.jobs[q.tail].minStep = job.context.step
        end
    end
end

function Queue.tick(q, timeBudgetMs, dispatch, flush, flushFirst)
    q.ticks = q.ticks + 1
    q.intervalTicks = q.intervalTicks + 1
    local start = q.clock()
    q.deadline = start + timeBudgetMs
    -- Give a carried publication batch first use of the shared budget.
    if flushFirst then flush(q) end
    local operations = 0
    while q.head <= q.tail and operations < q.quota do
        if q.clock() - start >= timeBudgetMs then
            q.cappedTicks = q.cappedTicks + 1
            break
        end
        local job = q.jobs[q.head]
        local node = job.node
        if not node or node.serial > job.cutoff then
            finish(q, job)
        else
            -- Advance before callbacks, which may remove this or adjacent plants.
            if job.reverse then job.node = node.prev else job.node = node.next end
            job.remaining = job.remaining - 1
            q.remaining = q.remaining - 1
            operations = operations + 1
            if node.alive and q.nodes[node.plant] == node then
                local begin = q.clock()
                dispatch(job.phase, node.plant, job.context, node)
                if job.minStep then job.minStep = math.min(job.minStep, node.lastStep) end
                local elapsed = q.clock() - begin
                local stat = q.phases[job.phase] or { calls = 0, totalMs = 0, maxMs = 0 }
                q.phases[job.phase] = stat
                stat.calls = stat.calls + 1
                stat.totalMs = stat.totalMs + elapsed
                stat.maxMs = math.max(stat.maxMs, elapsed)
                q.processed = q.processed + 1
            else
                q.skipped = q.skipped + 1
            end
            if not job.node or job.node.serial > job.cutoff then finish(q, job) end
        end
    end
    if flush and not flushFirst then flush(q) end
    q.maxTickMs = math.max(q.maxTickMs, q.clock() - start)
    q.deadline = nil
end

return Queue
