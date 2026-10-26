"""사내 Spark DataFrame 세 개를 Parquet 폴더로 저장하는 예제.

사내 모듈이 실행되는 Python/Spark 환경에서 이 파일을 import해 사용합니다.
이 파일 자체는 회사 데이터를 조회하거나 PostgreSQL에 적재하지 않습니다.
프로젝트 루트가 Python 경로에 있다면 다음과 같이 호출할 수 있습니다.

    from scripts.export_spark_demo import export_demo

    # 아래 세 변수는 사내 모듈로 조회한 실제 Spark DataFrame입니다.
    paths = export_demo(
        wafer_his_df=wafer_his_df,
        inline_df=inline_df,
        epm_df=epm_df,
        export_root="s3a://YOUR_EXPORT_BUCKET/demo/export_01",
    )

S3 경로는 실제 쓰기 가능한 경로로 바꿉니다. 사내 Spark의 S3 연결 설정이
필요합니다. 회사 PC에서 Spark를 로컬 모드로 실행할 때만 해당 PC의 경로
(예: file:///C:/demo-export/export_01)를 사용할 수 있습니다.
"""


def export_demo(
    *,
    wafer_his_df,
    inline_df,
    epm_df,
    export_root: str,
) -> dict[str, str]:
    """배치 DataFrame을 저장하고 데이터별 저장 경로를 반환합니다.

    원본 컬럼·행을 그대로 저장합니다. 필터·JOIN·pivot은 하지 않습니다.
    조회 단계에서 같은 프로젝트·Lot/Wafer를 연결할 수 있도록 범위를
    정하고, 필요한 선행 공정 이력과 Inline operation+parameter를 보존하세요.

    기존 경로는 덮어쓰지 않습니다. 중간에 실패하면 앞서 저장된 폴더가
    남을 수 있습니다. 실패한 묶음은 전달하지 말고 새 경로로 재실행하세요.
    """
    if not isinstance(export_root, str) or not export_root.strip().rstrip("/"):
        raise ValueError("실제 쓰기 가능한 저장 경로를 지정하세요.")

    root = export_root.strip().rstrip("/")
    frames = {
        "wafer_his": wafer_his_df,
        "inline": inline_df,
        "epm": epm_df,
    }

    # 쓰기 전에 세 입력을 모두 확인합니다. 스트리밍 DataFrame은 대상이 아닙니다.
    for name, frame in frames.items():
        if frame is None or not hasattr(frame, "isStreaming"):
            raise TypeError(f"{name}: 사내 모듈에서 받은 Spark DataFrame이 필요합니다.")
        if frame.isStreaming:
            raise ValueError(f"{name}: 배치 조회 결과를 사용하세요.")

    paths = {}
    for name, frame in frames.items():
        path = f"{root}/{name}"
        (
            frame.write
            .mode("errorifexists")
            .option("compression", "snappy")
            .parquet(path)
        )
        paths[name] = path
        print(f"{name}: 저장 완료")

    # 모든 저장이 성공한 경우에만 완료를 알립니다. 원시 측정 행은 출력하지 않습니다.
    print("세 데이터 저장 완료. 각 폴더의 모든 Parquet 파일을 함께 가져오세요.")
    return paths
