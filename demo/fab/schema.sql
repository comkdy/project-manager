-- Synthetic fab fixture. All six logical tables use LIST partitioning on lot_cd.
CREATE TABLE public.inline_data (
    lot_id text NOT NULL,
    wf_id integer NOT NULL CHECK (wf_id BETWEEN 1 AND 15),
    die_x integer NOT NULL,
    die_y integer NOT NULL,
    operation text NOT NULL,
    parameter text NOT NULL,
    value double precision,
    end_dt_tm timestamp with time zone NOT NULL,
    lot_cd text NOT NULL CHECK (lot_cd = left(lot_id, 4)),
    record_id bigint NOT NULL,
    PRIMARY KEY (lot_cd, record_id)
) PARTITION BY LIST (lot_cd);

CREATE TABLE public.epm_data (
    lot_id text NOT NULL,
    wf_id integer NOT NULL CHECK (wf_id BETWEEN 1 AND 15),
    die_x integer NOT NULL,
    die_y integer NOT NULL,
    parameter text NOT NULL,
    value double precision,
    end_dt_tm timestamp with time zone NOT NULL,
    lot_cd text NOT NULL CHECK (lot_cd = left(lot_id, 4)),
    record_id bigint NOT NULL,
    PRIMARY KEY (lot_cd, record_id)
) PARTITION BY LIST (lot_cd);

CREATE TABLE public.lot_his (
    lot_id text NOT NULL,
    operation text NOT NULL,
    remark text,
    end_dt_tm timestamp with time zone NOT NULL,
    lot_cd text NOT NULL CHECK (lot_cd = left(lot_id, 4)),
    record_id bigint NOT NULL,
    PRIMARY KEY (lot_cd, record_id)
) PARTITION BY LIST (lot_cd);

CREATE TABLE public.wafer_his (
    lot_id text NOT NULL,
    wf_id integer NOT NULL CHECK (wf_id BETWEEN 1 AND 15),
    operation text NOT NULL,
    recipe text NOT NULL,
    end_dt_tm timestamp with time zone NOT NULL,
    lot_cd text NOT NULL CHECK (lot_cd = left(lot_id, 4)),
    record_id bigint NOT NULL,
    eqp_id text NOT NULL,
    PRIMARY KEY (lot_cd, record_id)
) PARTITION BY LIST (lot_cd);

COMMENT ON COLUMN public.wafer_his.eqp_id IS 'Synthetic verification equipment ID; independent of measured outcomes; not a real equipment history.';

CREATE TABLE public.reject_his (
    lot_id text NOT NULL,
    wf_id integer NOT NULL CHECK (wf_id BETWEEN 1 AND 15),
    operation text NOT NULL,
    reject_reason text NOT NULL CHECK (reject_reason IN ('TEM 시편 제작', '단순 폐기')),
    remark text,
    end_dt_tm timestamp with time zone NOT NULL,
    lot_cd text NOT NULL CHECK (lot_cd = left(lot_id, 4)),
    record_id bigint NOT NULL,
    PRIMARY KEY (lot_cd, record_id),
    UNIQUE (lot_cd, record_id, lot_id, wf_id)
) PARTITION BY LIST (lot_cd);

CREATE TABLE public.tem_his (
    lot_id text NOT NULL,
    wf_id integer NOT NULL CHECK (wf_id BETWEEN 1 AND 15),
    reject_id bigint NOT NULL,
    serial_num text NOT NULL,
    end_dt_tm timestamp with time zone NOT NULL,
    lot_cd text NOT NULL CHECK (lot_cd = left(lot_id, 4)),
    record_id bigint NOT NULL,
    PRIMARY KEY (lot_cd, record_id),
    FOREIGN KEY (lot_cd, reject_id, lot_id, wf_id)
        REFERENCES public.reject_his (lot_cd, record_id, lot_id, wf_id)
) PARTITION BY LIST (lot_cd);

COMMENT ON TABLE public.inline_data IS '합성 테스트 데이터: wafer 위치·공정·parameter별 측정 이력. 마지막 값은 end_dt_tm, record_id 순서로 선택';
COMMENT ON TABLE public.epm_data IS '합성 테스트 데이터: wafer 위치·parameter별 전기 특성 측정 이력';
COMMENT ON TABLE public.lot_his IS '합성 테스트 데이터: lot 단위 공정 진행 이력 및 특이사항';
COMMENT ON TABLE public.wafer_his IS '합성 테스트 데이터: wafer 단위 공정·recipe 진행 이력';
COMMENT ON TABLE public.reject_his IS '합성 테스트 데이터: TEM 시편 제작 또는 단순 폐기에 따른 wafer 제외 이력';
COMMENT ON TABLE public.tem_his IS '합성 테스트 데이터: 폐기된 wafer의 TEM 시편별 촬영 이력. 동일 serial_num의 재촬영 허용';

CREATE INDEX ON public.inline_data (lot_cd, lot_id, wf_id, die_x, die_y, operation, parameter, end_dt_tm DESC, record_id DESC);
CREATE INDEX ON public.inline_data (end_dt_tm);
CREATE INDEX ON public.epm_data (lot_cd, lot_id, wf_id, die_x, die_y, parameter, end_dt_tm DESC, record_id DESC);
CREATE INDEX ON public.epm_data (end_dt_tm);
CREATE INDEX ON public.lot_his (lot_cd, lot_id, operation, end_dt_tm DESC, record_id DESC);
CREATE INDEX ON public.wafer_his (lot_cd, lot_id, wf_id, operation, end_dt_tm DESC, record_id DESC);
CREATE INDEX ON public.reject_his (lot_cd, lot_id, wf_id, end_dt_tm);
CREATE INDEX ON public.tem_his (lot_cd, lot_id, wf_id, serial_num, end_dt_tm);
