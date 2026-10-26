-- Additive synthetic schedule fixture. TAT is elapsed hours, including waiting.
CREATE TABLE public.lot_plan (
    lot_id text NOT NULL,
    operation_seq integer NOT NULL CHECK (operation_seq > 0),
    operation text NOT NULL,
    lot_cd text NOT NULL CHECK (lot_cd = left(lot_id, 4)),
    PRIMARY KEY (lot_cd, lot_id, operation_seq),
    UNIQUE (lot_cd, lot_id, operation)
) PARTITION BY LIST (lot_cd);

CREATE TABLE public.operation_tat (
    lot_id text NOT NULL,
    operation text NOT NULL,
    tat numeric(12, 3) NOT NULL CHECK (tat > 0),
    lot_cd text NOT NULL CHECK (lot_cd = left(lot_id, 4)),
    PRIMARY KEY (lot_cd, lot_id, operation),
    FOREIGN KEY (lot_cd, lot_id, operation)
        REFERENCES public.lot_plan (lot_cd, lot_id, operation)
) PARTITION BY LIST (lot_cd);

CREATE TABLE public.current_status (
    lot_id text NOT NULL,
    operation text NOT NULL,
    remark text,
    lot_cd text NOT NULL CHECK (lot_cd = left(lot_id, 4)),
    PRIMARY KEY (lot_cd, lot_id),
    FOREIGN KEY (lot_cd, lot_id, operation)
        REFERENCES public.lot_plan (lot_cd, lot_id, operation)
) PARTITION BY LIST (lot_cd);

COMMENT ON TABLE public.lot_plan IS '합성 테스트: lot별 전체 예정 공정 경로. operation_seq 순서. 현 fixture는 같은 공정 재방문 없음';
COMMENT ON TABLE public.operation_tat IS '합성 테스트: 완료한 공정의 경과 시간(hour), 대기 포함. 현재/미래 공정은 포함하지 않음';
COMMENT ON COLUMN public.operation_tat.tat IS '단위: 시간(hour), 소수 셋째 자리. 이전 공정 완료부터 해당 공정 완료까지의 경과 시간. 첫 공정은 시작을 아는 합성 WIP만 제공';
COMMENT ON TABLE public.current_status IS '합성 테스트: 기준 시점에 진행 중인 lot의 현재 공정, lot당 한 행. 완료한 lot은 제외';
COMMENT ON COLUMN public.current_status.lot_cd IS 'lot_id 앞 네 글자. 다른 테이블과 동일한 파티션/결합 키';
CREATE INDEX ON public.operation_tat (lot_cd, operation) INCLUDE (tat);
CREATE INDEX ON public.current_status (lot_cd, operation);
