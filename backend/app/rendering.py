"""Bounded, process-isolated Seaborn rendering from the shared chart query."""
from __future__ import annotations

import asyncio
import multiprocessing
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from .auth import get_current_user
from .schemas import CardSpec, QueryContext

router = APIRouter(prefix="/api", tags=["rendering"])
_render_slots = asyncio.Semaphore(2)


class RenderRequest(BaseModel):
    card: CardSpec
    context: QueryContext = QueryContext()
    format: Literal["png", "svg"] = "png"


def draw_chart(payload: dict, image_format: str) -> bytes:
    import io
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd
    import seaborn as sns
    from matplotlib import font_manager

    available = {font.name for font in font_manager.fontManager.ttflist}
    fonts = [f for f in ["Malgun Gothic", "Noto Sans CJK KR", "DejaVu Sans"] if f in available]
    sns.set_theme(style="whitegrid", font=fonts[0] if fonts else "DejaVu Sans")
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["svg.fonttype"] = "none"
    card, rows = payload["card"], payload["result"]["rows"]
    options = card.get("options", {})
    palette = options.get("palette", "viridis")
    palette = {"teal": "crest", "blues": "Blues", "warm": "rocket"}.get(palette, palette)
    if palette not in {"viridis", "magma", "crest", "Blues", "deep", "muted", "colorblind", "rocket", "coolwarm"}:
        palette = "viridis"
    if card.get("trellis") and rows:
        from .trellis_rendering import draw_trellis
        return draw_trellis(payload, image_format, palette)
    fig, ax = plt.subplots(figsize=(10, 6), dpi=140)
    try:
        frame = pd.DataFrame(rows)
        kind = card["kind"]
        if frame.empty:
            ax.text(.5, .5, "선택한 조건의 데이터가 없습니다", ha="center", va="center", transform=ax.transAxes)
            ax.set_axis_off()
        elif kind == "kpi":
            value = rows[0].get("value")
            label = "—" if value is None else f"{float(value):,.2f}"
            if (card.get("aggregate") == "yield" or options.get("value_format") == "percent") and value is not None:
                label += "%"
            ax.text(.5, .5, label, fontsize=48, fontweight="bold", color="#0f766e", ha="center", va="center", transform=ax.transAxes)
            if card.get("value_expression") is not None:
                ax.text(.5, .35, card.get("value_expression_name") or "직접 계산식", fontsize=14, ha="center", transform=ax.transAxes)
            ax.set_axis_off()
        elif kind in {"wafer"}:
            value_label = (card.get("value_expression_name") or "직접 계산식") if card.get("value_expression") is not None else payload["result"].get("meta", {}).get("value_label") or card.get("color") or "값"
            scatter = ax.scatter(frame["x"], frame["y"], c=pd.to_numeric(frame["value"], errors="coerce"), cmap=palette if palette not in {"deep", "muted", "colorblind"} else "viridis", marker="s", s=28)
            fig.colorbar(scatter, ax=ax, label=value_label)
            ax.set_aspect("equal")
            ax.grid(False)
        else:
            hue = "color" if "color" in frame.columns and frame["color"].notna().any() else None
            colors = {"hue": hue, "palette": palette} if hue else {"color": sns.color_palette(palette, 6)[2]}
            if kind == "scatter":
                sns.scatterplot(data=frame, x="x", y="y", ax=ax, s=18, alpha=.65, **colors)
            elif kind == "box":
                sns.boxplot(data=frame, x="x", y="y", ax=ax, fliersize=2, **colors)
            elif kind == "line":
                sns.lineplot(data=frame, x="x", y="y", estimator=None, ax=ax, **colors)
            else:
                # Histogram X values are numeric bin centres, not category slots.
                native = kind == "histogram" and options.get("x_scale") == "log"
                sns.barplot(data=frame, x="x", y="y", errorbar=None, native_scale=native, ax=ax, **colors)
            if len(frame["x"].unique()) > 12 and kind != "scatter":
                ticks = ax.get_xticks()
                ax.set_xticks(ticks[::max(1, len(ticks) // 10)])
                ax.tick_params(axis="x", rotation=35)
        ax.set_title(card["title"], loc="left", fontweight="bold", pad=20)
        if kind != "kpi":
            ax.set_xlabel(options.get("x_label") or card.get("x") or "", fontsize=options.get("x_font_size", 10))
            ax.tick_params(axis="x", labelsize=options.get("x_font_size", 10))
            ax.tick_params(axis="y", labelsize=options.get("y_font_size", 10))
            y_label = options.get("y_label") or card.get("y") or ""
            aggregate = card.get("aggregate", "none")
            if kind == "histogram":
                y_label = "Count"
            elif card.get("value_expression") is not None and kind not in {"wafer"}:
                y_label = card.get("value_expression_name") or "직접 계산식"
            elif kind in {"bar", "line"}:
                if aggregate == "count":
                    y_label = "Count"
                elif aggregate == "yield":
                    y_label = "수율 (%)"
            ax.set_ylabel(y_label, fontsize=options.get("y_font_size", 10))
            for axis in ("x", "y"):
                if options.get(f"{axis}_scale") == "log":
                    values = pd.to_numeric(frame.get(axis, pd.Series(dtype=float)), errors="coerce")
                    if not values.gt(0).any():
                        # Keep empty/nonpositive data renderable instead of failing
                        # in Matplotlib's logarithmic tick locator.
                        getattr(ax, f"set_{axis}lim")(1, 10)
                    getattr(ax, f"set_{axis}scale")("log", base=10, nonpositive="mask")
            if options.get("x_min") is not None or options.get("x_max") is not None:
                ax.set_xlim(left=options.get("x_min"), right=options.get("x_max"))
            if options.get("y_min") is not None or options.get("y_max") is not None:
                ax.set_ylim(bottom=options.get("y_min"), top=options.get("y_max"))
        meta = payload["result"].get("meta", {})
        fig.text(.01, .01, f"{meta.get('mode', '')} · {meta.get('returned_rows', len(rows)):,} rows · {meta.get('queried_at', '')}", fontsize=8, color="#64748b")
        fig.tight_layout(rect=(0, .035, 1, 1))
        output = io.BytesIO()
        fig.savefig(output, format=image_format, bbox_inches="tight", facecolor="white")
        return output.getvalue()
    finally:
        plt.close(fig)


def _render_child(connection, payload: dict, image_format: str):
    try:
        connection.send((True, draw_chart(payload, image_format)))
    except Exception as exc:
        connection.send((False, str(exc)[:500]))
    finally:
        connection.close()


def render_isolated(payload: dict, image_format: str) -> bytes:
    ctx = multiprocessing.get_context("spawn")
    reader, writer = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_render_child, args=(writer, payload, image_format), daemon=True)
    process.start()
    writer.close()
    try:
        if not reader.poll(60):
            raise TimeoutError("차트 이미지 생성 시간이 초과되었습니다.")
        ok, result = reader.recv()
        if not ok:
            raise ValueError("차트 이미지 생성에 실패했습니다: " + result)
        if len(result) > 12 * 1024 * 1024:
            raise ValueError("생성된 이미지가 너무 큽니다.")
        return result
    finally:
        reader.close()
        process.join(timeout=2)
        if process.is_alive():
            process.terminate()
            process.join(timeout=3)
        process.close()


@router.post("/render")
async def render_chart(request: RenderRequest, user=Depends(get_current_user)):
    from .query import chart_query
    card = request.card.model_copy(update={"renderer": "seaborn"})
    result = await run_in_threadpool(chart_query, card, request.context)
    payload = {"card": card.model_dump(mode="json"), "result": result}
    async with _render_slots:
        try:
            content = await run_in_threadpool(render_isolated, payload, request.format)
        except TimeoutError as exc:
            raise HTTPException(504, str(exc)) from exc
        except (ValueError, EOFError) as exc:
            raise HTTPException(422, str(exc)) from exc
    return Response(content, media_type="image/png" if request.format == "png" else "image/svg+xml",
                    headers={"Content-Disposition": f'inline; filename="project-manager-chart.{request.format}"', "Cache-Control": "private, no-store"})
