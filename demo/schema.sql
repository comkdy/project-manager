CREATE TABLE IF NOT EXISTS projects (
  id integer PRIMARY KEY, code text NOT NULL UNIQUE, name text NOT NULL,
  product text NOT NULL, process_node text NOT NULL
);
CREATE TABLE IF NOT EXISTS equipment (
  id integer PRIMARY KEY, code text NOT NULL UNIQUE, name text NOT NULL, equipment_type text NOT NULL
);
CREATE TABLE IF NOT EXISTS lots (
  id bigint PRIMARY KEY, project_id integer NOT NULL REFERENCES projects(id),
  lot_code text NOT NULL UNIQUE, product text NOT NULL, process_node text NOT NULL,
  measured_at timestamptz NOT NULL, UNIQUE (id, project_id)
);
CREATE TABLE IF NOT EXISTS wafers (
  id bigint PRIMARY KEY, project_id integer NOT NULL, lot_id bigint NOT NULL,
  lot_code text NOT NULL, wafer_code text NOT NULL UNIQUE, wafer_number integer NOT NULL,
  diameter_mm integer NOT NULL, measured_at timestamptz NOT NULL,
  UNIQUE (id, project_id), FOREIGN KEY (lot_id, project_id) REFERENCES lots(id, project_id)
);
CREATE TABLE IF NOT EXISTS inline_measurements (
  id bigint PRIMARY KEY, project_id integer NOT NULL, lot_id bigint NOT NULL,
  lot_code text NOT NULL, wafer_id bigint NOT NULL, wafer_code text NOT NULL,
  measured_at timestamptz NOT NULL, process_step text NOT NULL, recipe text NOT NULL,
  metric_name text NOT NULL, value double precision, unit text NOT NULL,
  lsl double precision NOT NULL, usl double precision NOT NULL,
  site_x integer NOT NULL, site_y integer NOT NULL, equipment_id integer NOT NULL REFERENCES equipment(id),
  FOREIGN KEY (wafer_id, project_id) REFERENCES wafers(id, project_id)
);
CREATE TABLE IF NOT EXISTS wat_measurements (
  id bigint PRIMARY KEY, project_id integer NOT NULL, lot_id bigint NOT NULL,
  lot_code text NOT NULL, wafer_id bigint NOT NULL, wafer_code text NOT NULL,
  measured_at timestamptz NOT NULL, test_run_id integer NOT NULL, metric_name text NOT NULL,
  value double precision, unit text NOT NULL, lsl double precision NOT NULL, usl double precision NOT NULL,
  site_x integer NOT NULL, site_y integer NOT NULL, equipment_id integer NOT NULL REFERENCES equipment(id),
  FOREIGN KEY (wafer_id, project_id) REFERENCES wafers(id, project_id)
);
CREATE TABLE IF NOT EXISTS die_results (
  id bigint PRIMARY KEY, project_id integer NOT NULL, lot_id bigint NOT NULL,
  lot_code text NOT NULL, wafer_id bigint NOT NULL, wafer_code text NOT NULL,
  measured_at timestamptz NOT NULL, test_run_id integer NOT NULL,
  die_x integer NOT NULL, die_y integer NOT NULL,
  status text NOT NULL CHECK (status IN ('tested','untested')), passed boolean, bin integer,
  CHECK ((status='untested' AND passed IS NULL AND bin IS NULL) OR
         (status='tested' AND passed IS NOT NULL AND bin IS NOT NULL)),
  FOREIGN KEY (wafer_id, project_id) REFERENCES wafers(id, project_id),
  UNIQUE (wafer_id,test_run_id,die_x,die_y)
);
CREATE OR REPLACE VIEW wafer_summary AS
WITH dies AS (
  SELECT wafer_id, project_id,
    count(*) FILTER (WHERE status='tested') AS tested_dies,
    count(*) FILTER (WHERE status='tested' AND passed) AS passed_dies
  FROM die_results WHERE test_run_id=1 GROUP BY wafer_id, project_id
), inline AS (
  SELECT wafer_id, project_id, avg(value) AS inline_thickness
  FROM inline_measurements WHERE metric_name='thickness' AND process_step='DEP'
  GROUP BY wafer_id, project_id
), wat AS (
  SELECT wafer_id, project_id, avg(value) AS wat_vth
  FROM wat_measurements WHERE metric_name='VTH' AND test_run_id=1
  GROUP BY wafer_id, project_id
)
SELECT w.id, w.project_id, p.name AS project_name, w.lot_id, w.lot_code,
       w.id AS wafer_id, w.wafer_code, w.measured_at,
       coalesce(d.tested_dies,0) AS tested_dies, coalesce(d.passed_dies,0) AS passed_dies,
       100.0*d.passed_dies/nullif(d.tested_dies,0) AS yield_pct,
       i.inline_thickness, t.wat_vth
FROM wafers w JOIN projects p ON p.id=w.project_id
LEFT JOIN dies d ON d.wafer_id=w.id AND d.project_id=w.project_id
LEFT JOIN inline i ON i.wafer_id=w.id AND i.project_id=w.project_id
LEFT JOIN wat t ON t.wafer_id=w.id AND t.project_id=w.project_id;
CREATE OR REPLACE VIEW wafer_correlation AS SELECT * FROM wafer_summary;
