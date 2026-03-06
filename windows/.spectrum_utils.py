from pathlib import Path
import argparse
import numpy as np

from bruker_tof_loader import load_bruker_tof


try:
    from scipy.signal import find_peaks as _sp_find_peaks, peak_prominences as _sp_prominences
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


def pick_peaks_numpy(
    x,
    y,
    *,
    min_mz=50.0,
    max_mz=None,
    min_rel_height=0.01,        # 最大強度に対する相対しきい値
    min_abs_height=None,
    min_prominence_rel=0.005,   # 最大強度に対する最小 prominence（ノイズ除去）
    min_distance_mz=0.5,        # ピーク間の最小距離（m/z単位）。Noneで無効
    min_distance_points=None,   # ピーク間の最小距離（点数）。min_distance_mz優先
    top_n=50,
):
    """
    scipy.signal.find_peaks (prominence付き) でピーク抽出。
    scipy未導入時は numpy フォールバック。
    戻り値: list[dict] (intensity降順)
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)

    # 基本マスク
    mask = np.isfinite(x) & np.isfinite(y)
    if min_mz is not None:
        mask &= (x >= float(min_mz))
    if max_mz is not None:
        mask &= (x <= float(max_mz))

    xm = x[mask]
    ym = y[mask]

    if xm.size < 3:
        return []

    y_max = float(np.nanmax(ym))
    if y_max <= 0:
        return []

    # しきい値
    threshold = 0.0
    if min_rel_height is not None:
        threshold = max(threshold, y_max * float(min_rel_height))
    if min_abs_height is not None:
        threshold = max(threshold, float(min_abs_height))

    # m/z → 点数に変換
    if min_distance_mz is not None and xm.size >= 2:
        mz_per_point = float(np.median(np.diff(xm)))
        if mz_per_point > 0:
            dist_points = max(1, int(round(float(min_distance_mz) / mz_per_point)))
        else:
            dist_points = 1
    elif min_distance_points is not None:
        dist_points = int(min_distance_points)
    else:
        dist_points = 1

    min_prom = y_max * float(min_prominence_rel) if min_prominence_rel is not None else 0.0

    if _HAS_SCIPY:
        # scipy: prominence + 距離フィルタ付き
        peaks_idx, props = _sp_find_peaks(
            ym,
            height=threshold if threshold > 0 else None,
            distance=dist_points,
            prominence=min_prom if min_prom > 0 else None,
        )
        if peaks_idx.size == 0:
            return []
        prominences = props.get("prominences", np.zeros(len(peaks_idx)))
    else:
        # フォールバック: 局所最大 + prominence 手計算
        cand = np.where((ym[1:-1] > ym[:-2]) & (ym[1:-1] >= ym[2:]))[0] + 1
        cand = cand[ym[cand] >= threshold]
        if cand.size == 0:
            return []

        # prominence: ピーク両側の最小値との差（より広い窓で計算）
        w = max(dist_points * 3, 30)
        proms = []
        for i in cand:
            l = max(0, i - w)
            r = min(len(ym) - 1, i + w)
            base = min(float(np.min(ym[l:i])) if i > l else ym[i],
                       float(np.min(ym[i:r + 1])) if r > i else ym[i])
            proms.append(float(ym[i]) - base)
        proms = np.array(proms)

        keep = proms >= min_prom
        cand = cand[keep]
        proms = proms[keep]
        if cand.size == 0:
            return []

        # 距離間引き（強度の高い順に greedy）
        order = np.argsort(ym[cand])[::-1]
        kept_idx, kept_prom = [], []
        for oi in order:
            i = int(cand[oi])
            if all(abs(i - j) >= dist_points for j in kept_idx):
                kept_idx.append(i)
                kept_prom.append(float(proms[oi]))
        peaks_idx = np.array(kept_idx, dtype=int)
        prominences = np.array(kept_prom)

    rows = []
    for k, i in enumerate(peaks_idx):
        rows.append({
            "mz": float(xm[i]),
            "intensity": float(ym[i]),
            "prominence": float(prominences[k]) if k < len(prominences) else 0.0,
            "point_index_in_filtered": int(i),
        })

    rows.sort(key=lambda d: d["intensity"], reverse=True)
    if top_n is not None:
        rows = rows[:int(top_n)]
    return rows


def pick_peaks_snr(
    x,
    y,
    *,
    min_mz=50.0,
    max_mz=None,
    snr_threshold=5.0,          # ローカルノイズの何倍以上を本物ピークとするか
    noise_window_mz=50.0,       # ノイズ推定に使う局所窓の幅（m/z単位）
    noise_percentile=10.0,      # 窓内の下位 X% をノイズフロアとみなす
    min_width_points=3,         # 最小ピーク幅（点数）。スパイクノイズを除去
    min_distance_mz=0.5,        # ピーク間の最小距離（m/z単位）
    top_n=50,
):
    """
    ローカル SNR + 幅フィルタ によるピーク検出。

    pick_peaks_numpy の代替として開発。
    - グローバルしきい値ではなく局所ノイズを推定するため、
      強度が何桁も異なるピークが混在するスペクトルに強い。
    - 幅フィルタでシングルポイントのスパイクノイズを除去。
    - scipy が入っていれば width フィルタを正確に適用。

    戻り値: list[dict] (intensity 降順)
      各 dict に mz / intensity / snr / width_points / point_index_in_filtered を含む
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)

    # m/z 範囲マスク
    mask = np.isfinite(x) & np.isfinite(y)
    if min_mz is not None:
        mask &= (x >= float(min_mz))
    if max_mz is not None:
        mask &= (x <= float(max_mz))
    xm = x[mask]
    ym = y[mask]
    if xm.size < 5:
        return []

    # ── ローカルノイズ推定 ──────────────────────────────────────
    # 各点について周辺 ±noise_window_mz/2 の下位パーセンタイルをノイズフロアとする
    mz_step = float(np.median(np.diff(xm))) if xm.size >= 2 else 0.01
    half_w = max(5, int(round((noise_window_mz / 2) / mz_step)))

    noise_floor = np.empty_like(ym)
    for i in range(len(ym)):
        lo = max(0, i - half_w)
        hi = min(len(ym), i + half_w + 1)
        noise_floor[i] = np.percentile(ym[lo:hi], noise_percentile)

    # ゼロ除算回避
    noise_floor = np.where(noise_floor > 0, noise_floor, np.nanmedian(ym[ym > 0]) * 0.001)

    # ── 局所最大の候補を抽出 ───────────────────────────────────
    # plateau 対策: 左側は strict >、右側は >=
    cand = np.where((ym[1:-1] > ym[:-2]) & (ym[1:-1] >= ym[2:]))[0] + 1
    if cand.size == 0:
        return []

    # SNR フィルタ
    snr = ym[cand] / noise_floor[cand]
    cand = cand[snr >= float(snr_threshold)]
    if cand.size == 0:
        return []

    # ── 幅フィルタ（スパイクノイズ除去） ─────────────────────
    if _HAS_SCIPY and min_width_points > 1:
        from scipy.signal import find_peaks as _fp, peak_widths as _pw
        # scipy の find_peaks で幅を測定（rel_height=0.5 = 半値幅）
        all_peaks, _ = _fp(ym, height=0)
        if all_peaks.size > 0:
            widths, *_ = _pw(ym, all_peaks, rel_height=0.5)
            wide_enough = set(int(all_peaks[k]) for k, w in enumerate(widths)
                              if w >= float(min_width_points))
        else:
            wide_enough = set()
        cand = np.array([i for i in cand if i in wide_enough], dtype=int)
    else:
        # フォールバック: ピーク頂点の左右に min_width_points/2 点以上が
        # 頂点強度の 50% を超えているかで幅を判定
        hw = max(1, min_width_points // 2)
        kept = []
        for i in cand:
            half_h = ym[i] * 0.5
            l = max(0, i - hw * 3)
            r = min(len(ym) - 1, i + hw * 3)
            left_ok  = any(ym[j] >= half_h for j in range(l, i))
            right_ok = any(ym[j] >= half_h for j in range(i + 1, r + 1))
            if left_ok and right_ok:
                kept.append(i)
        cand = np.array(kept, dtype=int)

    if cand.size == 0:
        return []

    # ── 距離間引き（greedy、SNR の高い順） ───────────────────
    dist_points = max(1, int(round(float(min_distance_mz) / mz_step)))
    order = cand[np.argsort(ym[cand] / noise_floor[cand])[::-1]]  # SNR 降順
    kept = []
    for idx in order:
        if all(abs(int(idx) - j) >= dist_points for j in kept):
            kept.append(int(idx))
    if not kept:
        return []

    rows = []
    for i in kept:
        rows.append({
            "mz": float(xm[i]),
            "intensity": float(ym[i]),
            "snr": float(ym[i] / noise_floor[i]),
            "noise_floor": float(noise_floor[i]),
            "point_index_in_filtered": int(i),
        })

    rows.sort(key=lambda d: d["intensity"], reverse=True)
    if top_n is not None:
        rows = rows[:int(top_n)]
    return rows


def is_bruker_measurement_dir(p: Path) -> bool:
    """
    Bruker TOF測定フォルダっぽいかを軽く判定する。
    厳密判定は loader 側に任せる。
    """
    if not p.is_dir():
        return False

    has_acqus = (p / "acqus").exists()
    has_fid = (p / "fid").exists()

    has_1r = False
    pdata = p / "pdata"
    if pdata.is_dir():
        for child in pdata.iterdir():
            if child.is_dir() and (child / "1r").exists():
                has_1r = True
                break

    # acqus があり、fid または processed(1r) があれば候補
    return has_acqus and (has_fid or has_1r)


def find_all_measurement_dirs(input_path: str | Path) -> list[Path]:
    """
    input_path 配下にある全ての Bruker 測定フォルダを返す（名前順）。
    input_path 自体が測定フォルダなら [input_path] を返す。
    """
    p = Path(input_path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"入力パスが存在しません: {p}")
    if is_bruker_measurement_dir(p):
        return [p]
    candidates = sorted(
        [d for d in p.rglob("*") if d.is_dir() and is_bruker_measurement_dir(d)],
        key=lambda d: d.name,
    )
    return candidates


def find_measurement_dir(input_path: str | Path) -> Path:
    """
    入力が measurement_dir 本体ならそのまま返す。
    そうでなければ配下を再帰探索して候補を探す。
    """
    p = Path(input_path).expanduser().resolve()

    if not p.exists():
        raise FileNotFoundError(f"入力パスが存在しません: {p}")

    # すでに測定フォルダならそのまま
    if is_bruker_measurement_dir(p):
        return p

    if not p.is_dir():
        raise NotADirectoryError(f"入力はディレクトリである必要があります: {p}")

    candidates = []
    for d in p.rglob("*"):
        if d.is_dir() and is_bruker_measurement_dir(d):
            candidates.append(d)

    if len(candidates) == 0:
        raise FileNotFoundError(
            "Bruker測定フォルダ候補が見つかりませんでした。\n"
            "（acqus と fid または pdata/*/1r を含むフォルダ）"
        )

    if len(candidates) > 1:
        msg = ["測定フォルダ候補が複数見つかりました。入力をもう少し絞ってください:"]
        for c in candidates:
            msg.append(f"  - {c}")
        raise RuntimeError("\n".join(msg))

    return candidates[0]


def main():
    print("a.py version: auto-find measurement_dir enabled + peak markers")

    parser = argparse.ArgumentParser(
        description="Bruker TOF peak labeling plot (Plotly, no CSV, flexible input path)"
    )
    parser.add_argument(
        "input_path",
        help="測定フォルダ or その親フォルダ（例: ./data/1Ref, ./data/sampleA）"
    )
    parser.add_argument("--procno", type=int, default=1, help="pdata/<procno> を使う (default: 1)")
    parser.add_argument("--min-mz", type=float, default=50.0, help="ピーク抽出/表示の最低 m/z (default: 50)")
    parser.add_argument("--max-mz", type=float, default=None, help="ピーク抽出/表示の最大 m/z (default: None)")
    parser.add_argument("--min-rel-height", type=float, default=0.01, help="最大強度に対する相対しきい値 (default: 0.01)")
    parser.add_argument("--min-abs-height", type=float, default=None, help="絶対強度しきい値 (default: None)")
    parser.add_argument("--min-distance-points", type=int, default=10, help="ピーク間の最小距離（点数）(default: 10)")
    parser.add_argument("--top-n", type=int, default=30, help="保持するピーク数（強度順）(default: 30)")
    parser.add_argument("--n-label", type=int, default=12, help="グラフに表示するラベル数 (default: 12)")
    parser.add_argument("--html-out", default="spectrum_peaks_interactive.html", help="出力HTML名")
    args = parser.parse_args()

    # plotly はここで import（未導入時のエラーを分かりやすく）
    try:
        import plotly.graph_objects as go
    except ModuleNotFoundError:
        raise SystemExit(
            "plotly が入っていません。\n"
            "次を実行してください:\n"
            "  python3 -m pip install plotly"
        )

    # 1) 入力パスから測定フォルダを解決
    measurement_dir = find_measurement_dir(args.input_path)
    print("resolved measurement_dir:", measurement_dir)

    # 2) データ読み込み
    res = load_bruker_tof(
        measurement_dir,
        procno=args.procno,
        prefer_processed=True,
        strictness="strict",
        axis_mode="mz",
    )

    print("status:", res["status"])
    print("selection:", res["metadata"]["selection"])

    x = res["arrays"]["mz"] if res["arrays"]["mz"] is not None else res["arrays"]["point_index"]
    y = res["arrays"]["intensity_primary"]

    if x is None or y is None:
        raise RuntimeError("x または y が取得できませんでした。res['arrays'] を確認してください。")

    print("x length:", len(x))
    print("y length:", len(y))
    print("x first 5:", x[:5])
    print("y first 5:", y[:5])

    # 3) ピーク抽出
    peaks = pick_peaks_numpy(
        x, y,
        min_mz=args.min_mz,
        max_mz=args.max_mz,
        min_rel_height=args.min_rel_height,
        min_abs_height=args.min_abs_height,
        min_distance_points=args.min_distance_points,
        top_n=args.top_n,
    )

    print(f"n_peaks (returned): {len(peaks)}")
    for i, p in enumerate(peaks[:min(12, len(peaks))], start=1):
        print(f"{i:2d}  m/z={p['mz']:.4f}  intensity={p['intensity']:.1f}")

    # 4) Plotly描画（赤丸あり・ピーク横ラベル）
    # pick_peaks_numpy と同じ条件で mask を作る（point_index_in_filtered を再利用するため）
    mask_plot = np.isfinite(x) & np.isfinite(y)
    if args.min_mz is not None:
        mask_plot &= (x >= args.min_mz)
    if args.max_mz is not None:
        mask_plot &= (x <= args.max_mz)

    xp = x[mask_plot]
    yp = y[mask_plot]

    # 表示用は m/z 順に並べると見やすい
    n_label = min(args.n_label, len(peaks))
    peaks_show = sorted(peaks[:n_label], key=lambda p: p["mz"])

    peak_x = []
    peak_y = []
    peak_text = []
    peak_textpos = []

    for i, p in enumerate(peaks_show):
        # pick_peaks_numpy が返した filtered配列インデックスを使う（再探索しない）
        j = int(p["point_index_in_filtered"])
        if j < 0 or j >= len(xp):
            continue

        xj = float(xp[j])
        yj = float(yp[j])

        peak_x.append(xj)
        peak_y.append(yj)
        peak_text.append(f"{xj:.2f}")  # ← ピーク横に表示する文字（m/z）

        # 少し重なりにくいように交互配置（左右も少し混ぜる）
        pos_cycle = ["top left", "bottom right", "top right", "bottom left"]
        peak_textpos.append(pos_cycle[i % len(pos_cycle)])

    print("len(peak_x):", len(peak_x))
    print("len(peak_y):", len(peak_y))
    print("first peak:", (peak_x[0], peak_y[0]) if peak_x else None)
    print("PEAK TRACE MODE = markers+text")

    fig = go.Figure()

    # スペクトル本体
    fig.add_trace(go.Scatter(
        x=xp,
        y=yp,
        mode="lines",
        name="Spectrum",
        line=dict(color="black", width=1),
        hovertemplate="m/z=%{x:.4f}<br>Intensity=%{y:.2f}<extra></extra>",
    ))

    # ピークラベル（赤丸あり）
    fig.add_trace(go.Scatter(
        x=peak_x,
        y=peak_y,
        mode="markers+text",
        name="Peaks",
        marker=dict(
            size=6,
            color="red",
            symbol="circle",
        ),
        text=peak_text,
        textposition=peak_textpos,
        textfont=dict(color="black", size=11),
        hovertemplate="m/z=%{x:.4f}<br>Intensity=%{y:.2f}<extra></extra>",
        showlegend=False,
    ))

    fig.update_layout(
        title="Bruker TOF Spectrum（ピーク横ラベル付き・インタラクティブ）",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="black"),
        xaxis=dict(
            title="m/z",
            showline=True,
            linecolor="black",
            showgrid=False,
            zeroline=False,
            rangeslider=dict(visible=True),
        ),
        yaxis=dict(
            title="Intensity",
            showline=True,
            linecolor="black",
            showgrid=False,
            zeroline=False,
        ),
        dragmode="zoom",
        hovermode="x",
    )

    fig.show()
    fig.write_html(args.html_out)
    print(f"saved: {args.html_out}")


if __name__ == "__main__":
    main()
