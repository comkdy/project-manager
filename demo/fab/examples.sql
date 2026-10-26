-- 1. Single-table pivot: MAIN, latest per operation/site, independent timestamps.
WITH ranked AS (
    SELECT lot_cd, lot_id, wf_id, die_x, die_y, operation, value,
           ROW_NUMBER() OVER (
               PARTITION BY lot_cd, lot_id, wf_id, die_x, die_y, operation
               ORDER BY end_dt_tm DESC, record_id DESC
           ) AS rn
    FROM public.inline_data
    WHERE lot_cd = 'XXXA' AND lot_id = 'XXXA1'
      AND parameter = 'MAIN' AND operation IN ('oper A CD', 'oper B CD')
), latest AS (
    SELECT * FROM ranked WHERE rn = 1
)
SELECT lot_cd, lot_id, wf_id, die_x, die_y,
       MAX(value) FILTER (WHERE operation = 'oper A CD') AS value_a,
       MAX(value) FILTER (WHERE operation = 'oper B CD') AS value_b
FROM latest
GROUP BY lot_cd, lot_id, wf_id, die_x, die_y
HAVING COUNT(value) FILTER (WHERE operation = 'oper A CD') > 0
   AND COUNT(value) FILTER (WHERE operation = 'oper B CD') > 0
ORDER BY lot_cd, lot_id, wf_id, die_x, die_y;

-- 2. Cross-table comparison: reduce both histories before joining.
WITH inline_last AS (
    SELECT DISTINCT ON (lot_cd, lot_id, wf_id, die_x, die_y)
           lot_cd, lot_id, wf_id, die_x, die_y, value AS inline_value, end_dt_tm AS inline_time
    FROM public.inline_data
    WHERE lot_cd = 'XXXB' AND operation = 'oper D CD' AND parameter = 'MAIN'
    ORDER BY lot_cd, lot_id, wf_id, die_x, die_y, end_dt_tm DESC, record_id DESC
), epm_last AS (
    SELECT DISTINCT ON (lot_cd, lot_id, wf_id, die_x, die_y)
           lot_cd, lot_id, wf_id, die_x, die_y, value AS epm_value, end_dt_tm AS epm_time
    FROM public.epm_data
    WHERE lot_cd = 'XXXB' AND parameter = 'PARA1'
    ORDER BY lot_cd, lot_id, wf_id, die_x, die_y, end_dt_tm DESC, record_id DESC
)
SELECT i.lot_cd, i.lot_id, i.wf_id, i.die_x, i.die_y,
       i.inline_value, e.epm_value, i.inline_time, e.epm_time
FROM inline_last i JOIN epm_last e USING (lot_cd, lot_id, wf_id, die_x, die_y)
WHERE i.inline_value IS NOT NULL AND e.epm_value IS NOT NULL
ORDER BY i.lot_cd, i.lot_id, i.wf_id, i.die_x, i.die_y;

-- 3. One row per TEM request/wafer, including requests without an image yet.
SELECT r.lot_cd, r.lot_id, r.wf_id, r.end_dt_tm AS rejected_at,
       COUNT(DISTINCT t.serial_num) AS specimen_count,
       COUNT(t.record_id) AS imaging_count, MAX(t.end_dt_tm) AS last_imaged_at
FROM public.reject_his r LEFT JOIN public.tem_his t
  ON t.lot_cd = r.lot_cd AND t.reject_id = r.record_id
 AND t.lot_id = r.lot_id AND t.wf_id = r.wf_id
WHERE r.lot_cd IN ('XXXA', 'XXXB', 'XXXC') AND r.reject_reason = 'TEM 시편 제작'
GROUP BY r.lot_cd, r.lot_id, r.wf_id, r.record_id, r.end_dt_tm
ORDER BY r.lot_cd, r.lot_id, r.wf_id;
