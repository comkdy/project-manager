-- WIP route + historical TAT. One row per planned operation, not per wafer.
-- TAT is hours; prefer the same lot_cd, then fall back to all lot groups.
-- No timestamps are invented. A missing duration remains NULL.
WITH group_tat AS (
    SELECT lot_cd, operation,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY tat) AS median_hours,
           count(*) AS samples
    FROM public.operation_tat
    GROUP BY lot_cd, operation
), all_tat AS (
    SELECT operation,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY tat) AS median_hours,
           count(*) AS samples
    FROM public.operation_tat
    GROUP BY operation
), completed AS (
    SELECT lot_cd, lot_id, operation, max(end_dt_tm) AS completed_at
    FROM public.lot_his
    GROUP BY lot_cd, lot_id, operation
)
SELECT p.lot_cd, p.lot_id, p.operation_seq, p.operation,
       CASE WHEN p.operation_seq < cp.operation_seq THEN 'completed'
            WHEN p.operation_seq = cp.operation_seq THEN 'current'
            ELSE 'upcoming' END AS status,
       h.completed_at,
       coalesce(g.median_hours, a.median_hours) AS expected_hours,
       coalesce(g.samples, a.samples, 0) AS tat_samples,
       CASE WHEN g.samples IS NOT NULL THEN 'same_lot_cd'
            WHEN a.samples IS NOT NULL THEN 'all_lot_cd'
            ELSE 'unavailable' END AS tat_basis,
       c.remark
FROM public.current_status c
JOIN public.lot_plan cp USING (lot_cd, lot_id, operation)
JOIN public.lot_plan p ON p.lot_cd=c.lot_cd AND p.lot_id=c.lot_id
LEFT JOIN completed h ON h.lot_cd=p.lot_cd AND h.lot_id=p.lot_id AND h.operation=p.operation
LEFT JOIN group_tat g ON g.lot_cd=p.lot_cd AND g.operation=p.operation
LEFT JOIN all_tat a ON a.operation=p.operation
ORDER BY p.lot_cd, p.lot_id, p.operation_seq;
