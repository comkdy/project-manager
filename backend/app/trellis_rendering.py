"""Shared-axis Trellis export for quantitative charts."""
import io
import math


def draw_trellis(payload, image_format, palette):
    import matplotlib.pyplot as plt
    import pandas as pd
    import seaborn as sns

    card, result = payload["card"], payload["result"]
    options, kind = card.get("options", {}), card["kind"]
    frame = pd.DataFrame(result["rows"])
    values = sorted(frame["trellis"].dropna().unique(), key=lambda v: str(v))
    panels = [("(빈 문자열)" if v == "" else str(v), frame[frame["trellis"] == v]) for v in values]
    if frame["trellis"].isna().any():
        panels.append(("(빈 값)", frame[frame["trellis"].isna()]))
    if len(panels) > 12:
        raise ValueError("Trellis는 최대 12개 패널을 표시합니다.")
    columns = min(len(panels), options.get("trellis_columns") or 2)
    rows = math.ceil(len(panels) / columns)
    fig, axes = plt.subplots(rows, columns, figsize=(max(7, columns * 4), rows * 3 + .8),
                             dpi=140, squeeze=False, sharex=True, sharey=True)
    try:
        hue = "color" if "color" in frame and frame["color"].notna().any() else None
        colors = {"color": sns.color_palette(palette, 6)[2]}
        if hue:
            if kind == "scatter" and pd.api.types.is_numeric_dtype(frame[hue]):
                colors = {"hue": hue, "palette": palette, "hue_norm": (frame[hue].min(), frame[hue].max())}
            else:
                groups = list(frame[hue].dropna().unique())
                colors = {"hue": hue, "hue_order": groups, "palette": dict(zip(groups, sns.color_palette(palette, len(groups))))}
        categories = list(frame["x"].dropna().unique())
        legend = {}
        for ax, (name, part) in zip(axes.flat, panels):
            if kind == "scatter":
                sns.scatterplot(data=part, x="x", y="y", ax=ax, s=18, alpha=.7, **colors)
            elif kind == "line":
                sns.lineplot(data=part, x="x", y="y", estimator=None, ax=ax, **colors)
            elif kind == "box":
                sns.boxplot(data=part, x="x", y="y", order=categories, ax=ax, fliersize=2, **colors)
            else:
                sns.barplot(data=part, x="x", y="y", ax=ax, errorbar=None,
                            native_scale=kind == "histogram", **({"order": categories} if kind == "bar" else {}), **colors)
            ax.set_title(name, fontsize=11, fontweight="bold", pad=10)
            ax.set_xlabel(options.get("x_label") or card.get("x") or "", fontsize=options.get("x_font_size", 10))
            y_label = options.get("y_label") or card.get("y") or ""
            if kind == "histogram" or card.get("aggregate") == "count":
                y_label = "Count"
            elif card.get("value_expression") is not None:
                y_label = card.get("value_expression_name") or "직접 계산식"
            elif card.get("aggregate") == "yield":
                y_label = "수율 (%)"
            ax.set_ylabel(y_label, fontsize=options.get("y_font_size", 10))
            ax.tick_params(axis="x", labelsize=options.get("x_font_size", 10))
            ax.tick_params(axis="y", labelsize=options.get("y_font_size", 10))
            if kind in {"bar", "box", "line"} and len(categories) > 8:
                ax.tick_params(axis="x", rotation=35)
            handles, labels = ax.get_legend_handles_labels()
            legend.update(zip(labels, handles))
            if ax.get_legend():
                ax.get_legend().remove()
        for ax in list(axes.flat)[len(panels):]:
            ax.set_visible(False)
        ax = axes.flat[0]
        for axis in ("x", "y"):
            if options.get(f"{axis}_scale") == "log":
                if not pd.to_numeric(frame[axis], errors="coerce").gt(0).any():
                    getattr(ax, f"set_{axis}lim")(1, 10)
                getattr(ax, f"set_{axis}scale")("log", base=10, nonpositive="mask")
            low, high = options.get(f"{axis}_min"), options.get(f"{axis}_max")
            if low is not None or high is not None:
                getattr(ax, f"set_{axis}lim")(low, high)
        if legend:
            fig.legend(legend.values(), legend.keys(), loc="center right", fontsize=9)
        fig.suptitle(card["title"], x=.04, ha="left", fontweight="bold")
        meta = result.get("meta", {})
        fig.text(.02, .01, f"{card['trellis']} · {len(panels)} panels · {meta.get('returned_rows', len(frame)):,} rows", fontsize=8, color="#64748b")
        fig.tight_layout(rect=(0, .04, .86 if legend else 1, .96))
        output = io.BytesIO()
        fig.savefig(output, format=image_format, bbox_inches="tight", facecolor="white")
        return output.getvalue()
    finally:
        plt.close(fig)
