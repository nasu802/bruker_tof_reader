from __future__ import annotations

import logging
import math
import re
from pathlib import Path
from typing import Any, Literal, Optional

import numpy as np


class BrukerTOFReadError(Exception):
    """Bruker TOF/XMASS 読み込み失敗を表す例外。"""
    pass


# =========================
# Result template / messages
# =========================

def _empty_result() -> dict[str, Any]:
    return {
        "status": "error",
        "metadata": {
            "format": {
                "vendor": "Bruker",
                "family": "XMASS/Flex",
                "sptype": None,
            },
            "selection": {
                "procno": None,
                "selected_source": None,   # "raw" | "processed" | None
                "fallback_used": False,
            },
            "acquisition": {
                "TD": None,
                "DW": None,
                "DELAY": None,
                "ML1": None,
                "ML2": None,
                "ML3": None,
                "BYTORDA": None,
            },
            "processing": {
                "BYTORDP": None,
                "NC_proc": None,
            },
            "axis": {
                "has_point_axis": False,
                "has_tof_axis": False,
                "has_mz_axis": False,
                "mz_calibration_model": None,
            },
            "validation": {
                "expected_points": None,
                "actual_points_fid": None,
                "actual_points_1r": None,
                "used_points": None,
                "trimmed": False,
            },
        },
        "arrays": {
            "point_index": None,
            "tof_ns": None,
            "tof_us": None,
            "mz": None,
            "fid_raw": None,
            "spectrum_1r_raw": None,
            "spectrum_1r_scaled": None,
            "intensity_primary": None,
        },
        "source_paths": {
            "measurement_dir": None,
            "acqus": None,
            "acqu": None,
            "fid": None,
            "sptype": None,
            "proc_dir": None,
            "1r": None,
            "procs": None,
            "proc": None,
            "exists_map": {},
            "resolved_procno": None,
        },
        "messages": {
            "infos": [],
            "warnings": [],
            "errors": [],
        },
    }


def _emit_logger(
    logger: Optional[logging.Logger],
    level: str,
    code: str,
    message: str,
) -> None:
    if logger is None:
        return
    text = f"[{code}] {message}"
    if level == "INFO":
        logger.info(text)
    elif level == "WARNING":
        logger.warning(text)
    elif level == "ERROR":
        logger.error(text)


def _append_msg(
    result: dict[str, Any],
    *,
    level: Literal["INFO", "WARNING", "ERROR"],
    code: str,
    message: str,
    logger: Optional[logging.Logger] = None,
    detail: Optional[str] = None,
    path: Optional[Path | str] = None,
    action: Optional[str] = None,
    suggestion: Optional[str] = None,
) -> None:
    rec: dict[str, Any] = {
        "code": code,
        "level": level,
        "message": message,
    }
    if detail is not None:
        rec["detail"] = detail
    if path is not None:
        rec["path"] = str(path)
    if action is not None:
        rec["action"] = action
    if suggestion is not None:
        rec["suggestion"] = suggestion

    if level == "INFO":
        result["messages"]["infos"].append(rec)
    elif level == "WARNING":
        result["messages"]["warnings"].append(rec)
    else:
        result["messages"]["errors"].append(rec)

    _emit_logger(logger, level, code, message)


def _info(result: dict[str, Any], code: str, message: str, logger=None, **kwargs) -> None:
    _append_msg(result, level="INFO", code=code, message=message, logger=logger, **kwargs)


def _warn(result: dict[str, Any], code: str, message: str, logger=None, **kwargs) -> None:
    _append_msg(result, level="WARNING", code=code, message=message, logger=logger, **kwargs)


def _fail(
    result: dict[str, Any],
    strictness: Literal["strict", "lenient"],
    code: str,
    message: str,
    logger=None,
    **kwargs,
) -> None:
    _append_msg(result, level="ERROR", code=code, message=message, logger=logger, **kwargs)
    if strictness == "strict":
        raise BrukerTOFReadError(f"{code}: {message}")


# =========================
# Parsers / binary readers
# =========================

def _parse_jcamp_params(path: Path) -> tuple[dict[str, str], str]:
    """
    Bruker パラメータファイル（acqus/procs 等）を簡易パース。
    '##$KEY= value' を抽出する。
    """
    txt = path.read_bytes().decode("latin-1", errors="replace")
    params: dict[str, str] = {}
    for line in txt.splitlines():
        m = re.match(r"^##\$(.+?)=\s*(.*)\s*$", line)
        if m:
            key, value = m.groups()
            params[key] = value.strip()
    return params, txt


def _parse_num(params: dict[str, str], key: str, typ=float) -> Optional[int | float]:
    if key not in params:
        return None
    raw = params[key].strip()

    # <...> 形式のときは中身を取り出す（文字列用が多いが保険）
    if raw.startswith("<") and raw.endswith(">"):
        raw = raw[1:-1]

    try:
        if typ is int:
            return int(float(raw))
        return float(raw)
    except Exception:
        return None


def _read_sptype(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    try:
        return path.read_bytes().decode("latin-1", errors="replace").strip().lower()
    except Exception:
        return None


def _read_binary_int32(path: Path, endian_flag: int) -> np.ndarray:
    """
    Bruker の int32 バイナリを読む。
    endian_flag: 0 -> little, 1 -> big
    """
    dtype = np.dtype("<i4" if int(endian_flag) == 0 else ">i4")
    raw = path.read_bytes()

    if len(raw) % dtype.itemsize != 0:
        raise BrukerTOFReadError("BRK-E-032: binary byte length is not aligned to int32")

    return np.frombuffer(raw, dtype=dtype)


# =========================
# Path resolution
# =========================

def _resolve_known_paths(measurement_dir: Path, procno: int) -> dict[str, Path]:
    proc_dir = measurement_dir / "pdata" / str(procno)
    return {
        "measurement_dir": measurement_dir,
        "acqus": measurement_dir / "acqus",
        "acqu": measurement_dir / "acqu",
        "fid": measurement_dir / "fid",
        "sptype": measurement_dir / "sptype",
        "proc_dir": proc_dir,
        "1r": proc_dir / "1r",
        "procs": proc_dir / "procs",
        "proc": proc_dir / "proc",
    }


# =========================
# Selection logic
# =========================

def _select_source(
    *,
    prefer_processed: bool,
    allow_raw_fallback: bool,
    allow_processed_fallback: bool,
    can_processed: bool,
    can_raw: bool,
    strictness: Literal["strict", "lenient"],
    result: dict[str, Any],
    logger: Optional[logging.Logger],
) -> tuple[Optional[Literal["raw", "processed"]], bool]:
    """
    戻り値:
        (selected_source, fallback_used)
    """
    if prefer_processed:
        if can_processed:
            _info(result, "BRK-I-050", "source='processed' を選択", logger=logger)
            return "processed", False
        if can_raw and allow_raw_fallback:
            _warn(
                result,
                "BRK-W-013",
                "processed 読込不可のため raw にフォールバックします",
                logger=logger,
                action="selected_source='raw'",
            )
            _info(result, "BRK-I-051", "source='raw' を選択", logger=logger)
            return "raw", True

        _fail(
            result,
            strictness,
            "BRK-E-060",
            "processed 優先だが processed 読込不可、かつ raw フォールバック不可",
            logger=logger,
        )
        return None, False

    # prefer raw
    if can_raw:
        _info(result, "BRK-I-051", "source='raw' を選択", logger=logger)
        return "raw", False
    if can_processed and allow_processed_fallback:
        _warn(
            result,
            "BRK-W-014",
            "raw 読込不可のため processed にフォールバックします",
            logger=logger,
            action="selected_source='processed'",
        )
        _info(result, "BRK-I-050", "source='processed' を選択", logger=logger)
        return "processed", True

    _fail(
        result,
        strictness,
        "BRK-E-061",
        "raw 優先だが raw 読込不可、かつ processed フォールバック不可",
        logger=logger,
    )
    return None, False


# =========================
# Main API
# =========================

def load_bruker_tof(
    measurement_dir: str | Path,
    *,
    procno: int = 1,
    prefer_processed: bool = True,
    allow_raw_fallback: bool = True,
    allow_processed_fallback: bool = True,
    strictness: Literal["strict", "lenient"] = "strict",
    axis_mode: Literal["auto", "mz", "tof", "point"] = "auto",
    require_tof: bool = False,
    logger: Optional[logging.Logger] = None,
) -> dict[str, Any]:
    """
    Bruker TOF/XMASS データ取り込み（初版）

    Parameters
    ----------
    measurement_dir
        測定フォルダ（例: 1Ref 相当）。フォルダ名は任意。
    procno
        pdata/<procno>/ を参照する処理番号（デフォルト: 1）
    prefer_processed
        Trueなら 1r を優先、Falseなら fid を優先
    allow_raw_fallback
        processed優先時に processed不可なら raw へフォールバックするか
    allow_processed_fallback
        raw優先時に raw不可なら processed へフォールバックするか
    strictness
        "strict" | "lenient"
    axis_mode
        "auto" | "mz" | "tof" | "point"
    require_tof
        sptype != "tof" を厳しく扱うか（strict時はエラー、lenient時は警告）
    logger
        任意の logging.Logger

    Returns
    -------
    dict
        status / metadata / arrays / source_paths / messages を含む構造化結果
    """
    if strictness not in ("strict", "lenient"):
        raise ValueError("strictness must be 'strict' or 'lenient'")
    if axis_mode not in ("auto", "mz", "tof", "point"):
        raise ValueError("axis_mode must be one of: auto, mz, tof, point")
    if procno < 1:
        raise ValueError("procno must be >= 1")

    result = _empty_result()
    result["metadata"]["selection"]["procno"] = int(procno)
    result["source_paths"]["resolved_procno"] = int(procno)

    # ---- Path normalization
    md = Path(measurement_dir).expanduser()
    try:
        md = md.resolve()
    except Exception:
        md = md.absolute()

    result["source_paths"]["measurement_dir"] = str(md)

    # ---- Existence / type check
    if not md.exists():
        _fail(result, strictness, "BRK-E-001", "measurement_dir が存在しません", logger=logger, path=md)
        return result
    if not md.is_dir():
        _fail(result, strictness, "BRK-E-002", "measurement_dir がディレクトリではありません", logger=logger, path=md)
        return result

    # ---- Resolve known paths
    paths = _resolve_known_paths(md, procno)
    for k, p in paths.items():
        result["source_paths"][k] = str(p)
    result["source_paths"]["exists_map"] = {
        k: p.exists() for k, p in paths.items() if k != "measurement_dir"
    }

    _info(result, "BRK-I-052", f"procno={procno} を使用", logger=logger, path=paths["proc_dir"])

    # ---- Format recognition (Bruker TOF-ish)
    acqus_params: dict[str, str] = {}
    procs_params: dict[str, str] = {}

    if not paths["acqus"].exists():
        # strictでは仕様上必須
        if strictness == "strict":
            _fail(
                result,
                strictness,
                "BRK-E-010",
                "acqus が見つからないため形式認識/軸生成ができません",
                logger=logger,
                path=paths["acqus"],
            )
            return result
        else:
            _warn(
                result,
                "BRK-W-010",
                "acqus が見つかりません（形式認識が弱く、m/z/tof軸生成に制約が出ます）",
                logger=logger,
                path=paths["acqus"],
            )
    else:
        try:
            acqus_params, _ = _parse_jcamp_params(paths["acqus"])
        except Exception as e:
            _fail(
                result,
                strictness,
                "BRK-E-011",
                "acqus の読み込みに失敗しました",
                logger=logger,
                path=paths["acqus"],
                detail=str(e),
            )
            return result

    sptype = _read_sptype(paths["sptype"])
    result["metadata"]["format"]["sptype"] = sptype

    if not paths["sptype"].exists():
        _warn(result, "BRK-W-011", "sptype が見つかりません", logger=logger, path=paths["sptype"])
    elif sptype != "tof":
        msg = f"sptype='{sptype}' のため TOF想定と一致しません"
        if require_tof and strictness == "strict":
            _fail(result, strictness, "BRK-E-020", msg, logger=logger, path=paths["sptype"])
            return result
        _warn(result, "BRK-W-020", msg, logger=logger, path=paths["sptype"])

    if paths["procs"].exists():
        try:
            procs_params, _ = _parse_jcamp_params(paths["procs"])
        except Exception as e:
            if strictness == "strict":
                _fail(
                    result,
                    strictness,
                    "BRK-E-012",
                    "procs の読み込みに失敗しました",
                    logger=logger,
                    path=paths["procs"],
                    detail=str(e),
                )
                return result
            else:
                _warn(
                    result,
                    "BRK-W-012",
                    "procs の読み込みに失敗したため、1rの解釈を制限して継続します",
                    logger=logger,
                    path=paths["procs"],
                    detail=str(e),
                )
    else:
        if paths["1r"].exists():
            _warn(
                result,
                "BRK-W-012",
                "procs が見つからないため、1rは読み込みますがスケーリング未適用の可能性があります",
                logger=logger,
                path=paths["procs"],
                suggestion="強度値は相対比較として扱ってください",
            )

    # ---- Capability check (existence-based)
    raw_exists = paths["fid"].exists()
    processed_exists = paths["1r"].exists()

    if not raw_exists and not processed_exists:
        _fail(
            result,
            strictness,
            "BRK-E-062",
            "fid と 1r の両方が見つからないため読み込みできません",
            logger=logger,
            path=md,
        )
        return result

    # raw は初版では acqus を必須（軸生成/解釈のため）
    can_raw = raw_exists and paths["acqus"].exists()
    can_processed = processed_exists  # strict詳細チェックは後段（BYTORDP等）

    selected_source, fallback_used = _select_source(
        prefer_processed=prefer_processed,
        allow_raw_fallback=allow_raw_fallback,
        allow_processed_fallback=allow_processed_fallback,
        can_processed=can_processed,
        can_raw=can_raw,
        strictness=strictness,
        result=result,
        logger=logger,
    )
    if selected_source is None:
        return result

    result["metadata"]["selection"]["selected_source"] = selected_source
    result["metadata"]["selection"]["fallback_used"] = fallback_used

    # ---- Parse numeric params (acqus/procs)
    acq_keys = [
        ("TD", int),
        ("DW", float),
        ("DELAY", float),
        ("ML1", float),
        ("ML2", float),
        ("ML3", float),
        ("BYTORDA", int),
    ]
    acq_num: dict[str, Optional[int | float]] = {}
    for key, typ in acq_keys:
        val = _parse_num(acqus_params, key, typ) if acqus_params else None
        acq_num[key] = val
        result["metadata"]["acquisition"][key] = val

    proc_keys = [
        ("BYTORDP", int),
        ("NC_proc", int),
    ]
    proc_num: dict[str, Optional[int | float]] = {}
    for key, typ in proc_keys:
        val = _parse_num(procs_params, key, typ) if procs_params else None
        proc_num[key] = val
        result["metadata"]["processing"][key] = val

    # ---- Read binary arrays
    fid_raw: Optional[np.ndarray] = None
    spectrum_1r_raw: Optional[np.ndarray] = None
    spectrum_1r_scaled: Optional[np.ndarray] = None

    # raw
    if raw_exists:
        bytorda = acq_num["BYTORDA"]
        if bytorda is None:
            if strictness == "strict" and selected_source == "raw":
                _fail(
                    result,
                    strictness,
                    "BRK-E-021",
                    "BYTORDA が取得できないため fid を解釈できません",
                    logger=logger,
                    path=paths["acqus"],
                )
                return result
            # lenient または raw未選択時は little-endian 仮定
            bytorda = 0
            _warn(
                result,
                "BRK-W-021",
                "BYTORDA が取得できないため little-endian と仮定して fid を解釈します",
                logger=logger,
                path=paths["acqus"],
            )
            result["metadata"]["acquisition"]["BYTORDA"] = bytorda

        try:
            fid_raw = _read_binary_int32(paths["fid"], int(bytorda)).astype(np.float64)
        except BrukerTOFReadError as e:
            msg = str(e)
            if "BRK-E-032" in msg:
                _fail(
                    result,
                    strictness,
                    "BRK-E-032",
                    "fid のバイナリ長が int32 境界に整合しません",
                    logger=logger,
                    path=paths["fid"],
                )
            else:
                _fail(
                    result,
                    strictness,
                    "BRK-E-022",
                    "fid の読み込みに失敗しました",
                    logger=logger,
                    path=paths["fid"],
                    detail=msg,
                )
            if selected_source == "raw":
                return result
            fid_raw = None
        except Exception as e:
            _fail(
                result,
                strictness,
                "BRK-E-022",
                "fid の読み込みに失敗しました",
                logger=logger,
                path=paths["fid"],
                detail=str(e),
            )
            if selected_source == "raw":
                return result
            fid_raw = None

    # processed (1r)
    if processed_exists:
        bytordp = proc_num["BYTORDP"]
        if bytordp is None:
            if strictness == "strict" and selected_source == "processed":
                _fail(
                    result,
                    strictness,
                    "BRK-E-023",
                    "BYTORDP が取得できないため 1r を解釈できません（procs 確認）",
                    logger=logger,
                    path=paths["procs"],
                )
                return result
            bytordp = 0
            _warn(
                result,
                "BRK-W-022",
                "BYTORDP が取得できないため little-endian と仮定して 1r を解釈します",
                logger=logger,
                path=paths["procs"],
            )
            result["metadata"]["processing"]["BYTORDP"] = bytordp

        try:
            spectrum_1r_raw = _read_binary_int32(paths["1r"], int(bytordp)).astype(np.float64)
        except BrukerTOFReadError as e:
            msg = str(e)
            if "BRK-E-032" in msg:
                _fail(
                    result,
                    strictness,
                    "BRK-E-032",
                    "1r のバイナリ長が int32 境界に整合しません",
                    logger=logger,
                    path=paths["1r"],
                )
            else:
                _fail(
                    result,
                    strictness,
                    "BRK-E-024",
                    "1r の読み込みに失敗しました",
                    logger=logger,
                    path=paths["1r"],
                    detail=msg,
                )
            if selected_source == "processed":
                return result
            spectrum_1r_raw = None
        except Exception as e:
            _fail(
                result,
                strictness,
                "BRK-E-024",
                "1r の読み込みに失敗しました",
                logger=logger,
                path=paths["1r"],
                detail=str(e),
            )
            if selected_source == "processed":
                return result
            spectrum_1r_raw = None

        if spectrum_1r_raw is not None:
            nc_proc = proc_num["NC_proc"]
            if nc_proc is None:
                _warn(
                    result,
                    "BRK-W-033",
                    "NC_proc が取得できないため 1r のスケーリング未適用で返します",
                    logger=logger,
                    path=paths["procs"],
                    suggestion="強度値は相対比較として扱ってください",
                )
                spectrum_1r_scaled = spectrum_1r_raw.copy()
            else:
                spectrum_1r_scaled = spectrum_1r_raw * (2.0 ** int(nc_proc))

    actual_points_fid = int(fid_raw.size) if fid_raw is not None else None
    actual_points_1r = int(spectrum_1r_raw.size) if spectrum_1r_raw is not None else None
    result["metadata"]["validation"]["actual_points_fid"] = actual_points_fid
    result["metadata"]["validation"]["actual_points_1r"] = actual_points_1r

    # ---- Length validation (TD + cross-array)
    TD = acq_num["TD"]
    if paths["acqus"].exists() and TD is None:
        # acqus はあるのにTD取れない
        if strictness == "strict":
            _fail(
                result,
                strictness,
                "BRK-E-030",
                "TD が取得できません",
                logger=logger,
                path=paths["acqus"],
            )
            return result
        _warn(
            result,
            "BRK-W-030",
            "TD が取得できないため、実データ長ベースで点数を決定します",
            logger=logger,
            path=paths["acqus"],
        )

    if TD is not None and int(TD) <= 0:
        _fail(
            result,
            strictness,
            "BRK-E-030",
            f"TD が不正です: {TD}",
            logger=logger,
            path=paths["acqus"],
        )
        return result

    lengths = []
    if fid_raw is not None:
        lengths.append(int(fid_raw.size))
    if spectrum_1r_raw is not None:
        lengths.append(int(spectrum_1r_raw.size))
    if TD is not None:
        lengths.append(int(TD))

    if not lengths:
        _fail(result, strictness, "BRK-E-063", "読み込み可能な配列がありません", logger=logger)
        return result

    used_points = min(lengths)
    result["metadata"]["validation"]["expected_points"] = int(TD) if TD is not None else None
    result["metadata"]["validation"]["used_points"] = int(used_points)

    trimmed = False

    if TD is not None:
        if actual_points_fid is not None and actual_points_fid != int(TD):
            if strictness == "strict":
                _fail(
                    result,
                    strictness,
                    "BRK-E-031",
                    f"TD({int(TD)}) と fid 実点数({actual_points_fid}) が一致しません",
                    logger=logger,
                    path=paths["fid"],
                )
                return result
            trimmed = True
            _warn(
                result,
                "BRK-W-031",
                f"TD({int(TD)}) と fid 実点数({actual_points_fid}) が不一致のため切り詰めます",
                logger=logger,
                path=paths["fid"],
                action=f"used_points={used_points}",
            )

        if actual_points_1r is not None and actual_points_1r != int(TD):
            if strictness == "strict":
                _fail(
                    result,
                    strictness,
                    "BRK-E-031",
                    f"TD({int(TD)}) と 1r 実点数({actual_points_1r}) が一致しません",
                    logger=logger,
                    path=paths["1r"],
                )
                return result
            trimmed = True
            _warn(
                result,
                "BRK-W-031",
                f"TD({int(TD)}) と 1r 実点数({actual_points_1r}) が不一致のため切り詰めます",
                logger=logger,
                path=paths["1r"],
                action=f"used_points={used_points}",
            )

    if (
        actual_points_fid is not None
        and actual_points_1r is not None
        and actual_points_fid != actual_points_1r
    ):
        if strictness == "strict":
            _fail(
                result,
                strictness,
                "BRK-E-033",
                f"fid 実点数({actual_points_fid}) と 1r 実点数({actual_points_1r}) が一致しません",
                logger=logger,
            )
            return result
        trimmed = True
        _warn(
            result,
            "BRK-W-034",
            "fid と 1r の実点数が不一致のため min 長で切り詰めます",
            logger=logger,
            action=f"used_points={used_points}",
        )

    result["metadata"]["validation"]["trimmed"] = bool(trimmed)

    # trim all arrays to used_points
    if fid_raw is not None:
        fid_raw = fid_raw[:used_points]
    if spectrum_1r_raw is not None:
        spectrum_1r_raw = spectrum_1r_raw[:used_points]
    if spectrum_1r_scaled is not None:
        spectrum_1r_scaled = spectrum_1r_scaled[:used_points]

    # ---- Axis generation
    point_index = np.arange(used_points, dtype=np.int64)
    tof_ns: Optional[np.ndarray] = None
    tof_us: Optional[np.ndarray] = None
    mz: Optional[np.ndarray] = None

    can_point = True
    can_tof = (acq_num["DW"] is not None) and (acq_num["DELAY"] is not None)
    can_mz = (
        can_tof
        and (acq_num["ML1"] is not None)
        and (acq_num["ML2"] is not None)
        and (acq_num["ML3"] is not None)
        and (float(acq_num["ML1"]) != 0.0)
    )

    # auto のとき要求レベルを内部決定
    requested_axis = axis_mode
    if axis_mode == "auto":
        if can_mz:
            requested_axis = "mz"
        elif can_tof:
            requested_axis = "tof"
        else:
            requested_axis = "point"

    # TOF requirement
    if requested_axis in ("tof", "mz") and not can_tof:
        if strictness == "strict":
            _fail(
                result,
                strictness,
                "BRK-E-041",
                f"axis_mode='{axis_mode}' ですが TOF軸生成に必要なパラメータが不足しています",
                logger=logger,
                path=paths["acqus"],
            )
            return result
        _warn(
            result,
            "BRK-W-041",
            "TOF軸生成不可のため point 軸へ降格します",
            logger=logger,
            path=paths["acqus"],
        )
        requested_axis = "point"

    # m/z requirement
    if requested_axis == "mz" and not can_mz:
        if strictness == "strict":
            _fail(
                result,
                strictness,
                "BRK-E-040",
                "axis_mode='mz' ですが m/z軸生成に必要なパラメータが不足しています",
                logger=logger,
                path=paths["acqus"],
            )
            return result
        _warn(
            result,
            "BRK-W-041",
            "m/z軸生成不可のため point 軸へ降格します",
            logger=logger,
            path=paths["acqus"],
        )
        requested_axis = "point"

    # point axis (always)
    if can_point:
        result["arrays"]["point_index"] = point_index
        result["metadata"]["axis"]["has_point_axis"] = True

    # tof axis
    if requested_axis in ("tof", "mz"):
        tof_ns = float(acq_num["DELAY"]) + point_index.astype(np.float64) * float(acq_num["DW"])
        tof_us = tof_ns / 1000.0
        result["arrays"]["tof_ns"] = tof_ns
        result["arrays"]["tof_us"] = tof_us
        result["metadata"]["axis"]["has_tof_axis"] = True

    # mz axis (Bruker quadratic)
    if requested_axis == "mz":
        ML1 = float(acq_num["ML1"])
        ML2 = float(acq_num["ML2"])
        ML3 = float(acq_num["ML3"])

        # readBrukerFlexData系の二次校正式（√m/z について解く）
        # A*(sqrt(m))^2 + B*sqrt(m) + C = 0
        # A = ML3
        # B = sqrt(1e12 / ML1)
        # C = ML2 - tof_ns
        A = ML3
        B = math.sqrt(1.0e12 / ML1)
        C = ML2 - tof_ns  # vector

        if abs(A) < 1e-15:
            # 線形退化ケース
            mz = (C * C) / (B * B)
        else:
            disc = (B * B) - (4.0 * A * C)
            neg_mask = disc < 0
            if np.any(neg_mask):
                n_neg = int(np.sum(neg_mask))
                _warn(
                    result,
                    "BRK-W-042",
                    "校正式計算で判別式が負の点があり、該当 m/z を NaN にします",
                    logger=logger,
                    action=f"n_negative={n_neg}",
                )
                disc = np.where(neg_mask, np.nan, disc)
            mz = ((-B + np.sqrt(disc)) / (2.0 * A)) ** 2

        result["arrays"]["mz"] = mz
        result["metadata"]["axis"]["has_mz_axis"] = True
        result["metadata"]["axis"]["mz_calibration_model"] = "bruker_quadratic"

    # ---- Store signal arrays
    result["arrays"]["fid_raw"] = fid_raw
    result["arrays"]["spectrum_1r_raw"] = spectrum_1r_raw
    result["arrays"]["spectrum_1r_scaled"] = spectrum_1r_scaled

    if selected_source == "processed":
        result["arrays"]["intensity_primary"] = (
            spectrum_1r_scaled if spectrum_1r_scaled is not None else spectrum_1r_raw
        )
    elif selected_source == "raw":
        result["arrays"]["intensity_primary"] = fid_raw

    # ---- Final status
    warnings_count = len(result["messages"]["warnings"])
    errors_count = len(result["messages"]["errors"])

    axis_satisfied = True
    if axis_mode == "mz" and not result["metadata"]["axis"]["has_mz_axis"]:
        axis_satisfied = False
    elif axis_mode == "tof" and not result["metadata"]["axis"]["has_tof_axis"]:
        axis_satisfied = False
    elif axis_mode == "point" and not result["metadata"]["axis"]["has_point_axis"]:
        axis_satisfied = False

    # strictでここまで来ていれば通常エラー無し
    if strictness == "lenient" and not axis_satisfied:
        result["status"] = "partial"
    elif errors_count > 0 and strictness == "lenient":
        # lenientで内部回復したケースの記録がある場合
        result["status"] = "ok_with_warnings" if warnings_count > 0 else "partial"
    elif warnings_count > 0:
        result["status"] = "ok_with_warnings"
    else:
        result["status"] = "ok"

    return result


# =========================
# Optional helper: simple usage
# =========================

if __name__ == "__main__":
    # 例:
    #   res = load_bruker_tof("/path/to/1Ref", strictness="strict", axis_mode="mz")
    #   x = res["arrays"]["mz"] if res["arrays"]["mz"] is not None else res["arrays"]["point_index"]
    #   y = res["arrays"]["intensity_primary"]
    #
    # 必要に応じてここに CLI 実装を追加してください。
    pass