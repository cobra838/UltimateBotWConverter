import struct
import logging
from pathlib import Path

import oead

logger = logging.getLogger(__name__)

_EMTR = b"EMTR"
# Within the binary data section of each EMTR node:
#   +0x00: 16 bytes zeros
#   +0x10: 64-byte name string
#   +0x50: parameter data
# The binary data section start offset is stored at node+0x14 (BE or LE uint32).
_BIN_NAME_REL = 0x10
_BIN_PARAM_REL = 0x50


def _decomp(data: bytes) -> bytes:
    return oead.yaz0.decompress(data) if data[:4] == b"Yaz0" else data


def _gpu_section_start(data: bytes, is_wiiu: bool) -> int:
    """Return offset where GPU-specific data starts (textures/shaders)."""
    tags = (b"TEXA", b"SHDA") if is_wiiu else (b"BNSH", b"BNTX")
    limit = len(data)
    for tag in tags:
        idx = data.find(tag)
        if idx != -1 and idx < limit:
            limit = idx
    return limit


def _find_emtrs(data: bytes, is_wiiu: bool) -> list:
    """Return list of (abs_offset, size, name, param_bytes, param_start) in file order."""
    endian = ">" if is_wiiu else "<"
    limit = _gpu_section_start(data, is_wiiu)
    result = []
    pos = 0
    while pos < limit:
        idx = data.find(_EMTR, pos, limit)
        if idx == -1:
            break
        if idx + 0x20 > len(data):
            break
        size = struct.unpack_from(endian + "I", data, idx + 4)[0]
        # binary data section start offset is stored at node+0x14
        bin_start = struct.unpack_from(endian + "I", data, idx + 0x14)[0]
        name_off = bin_start + _BIN_NAME_REL
        param_off = bin_start + _BIN_PARAM_REL
        if param_off >= size or idx + size > len(data) or name_off + 64 > size:
            pos = idx + 4
            continue
        name_bytes = data[idx + name_off : idx + name_off + 64]
        name = name_bytes.split(b"\x00")[0].decode("ascii", errors="replace")
        if not name or not name.isprintable():
            pos = idx + 4
            continue
        param = data[idx + param_off : idx + size]
        result.append((idx, size, name, param, idx + param_off))
        pos = idx + size
    return result


def _index_emtrs_by_name(emtrs: list, label: str) -> dict:
    index = {}
    duplicates = set()
    for emtr in emtrs:
        name = emtr[2]
        if name in index:
            duplicates.add(name)
        else:
            index[name] = emtr

    for name in duplicates:
        logger.warning("%s has duplicate EMTR %r; skipping duplicate-name patch", label, name)
        index.pop(name, None)

    return index


def convert_sesetlist(mod_wiiu: bytes, stock_wiiu: bytes, stock_switch: bytes) -> bytes:
    """
    Produce a Switch-compatible sesetlist from a modded WiiU file.

    Takes stock Switch VFXB as the base and patches each EMTR's parameter
    block with the endian-swapped diff between mod_wiiu and stock_wiiu.
    Textures and shaders always come from stock_switch (GPU-API-specific).
    Returns bytes in the same compression state as stock_switch.
    """
    mod_raw = _decomp(mod_wiiu)
    stock_w_raw = _decomp(stock_wiiu)
    stock_s_raw = _decomp(stock_switch)

    if mod_raw[:4] != b"EFTB":
        return stock_switch

    if mod_raw == stock_w_raw:
        return stock_switch

    mod_emtrs = _find_emtrs(mod_raw, is_wiiu=True)
    stock_w_emtrs = _find_emtrs(stock_w_raw, is_wiiu=True)
    stock_s_emtrs = _find_emtrs(stock_s_raw, is_wiiu=False)

    stock_w_by_name = _index_emtrs_by_name(stock_w_emtrs, "stock WiiU sesetlist")
    stock_s_by_name = _index_emtrs_by_name(stock_s_emtrs, "stock Switch sesetlist")

    result = bytearray(stock_s_raw)
    patched = 0

    for _, _, mod_name, mod_params, _ in mod_emtrs:
        stock_w_emtr = stock_w_by_name.get(mod_name)
        if stock_w_emtr is None:
            logger.warning("EMTR %r not found in stock WiiU sesetlist; skipping", mod_name)
            continue

        stock_s_emtr = stock_s_by_name.get(mod_name)
        if stock_s_emtr is None:
            logger.warning("EMTR %r not found in stock Switch sesetlist; skipping", mod_name)
            continue

        _, _, _, stock_w_params, _ = stock_w_emtr
        _, _, _, stock_s_params, s_param_start = stock_s_emtr
        if len(mod_params) != len(stock_w_params):
            logger.warning("EMTR %r param size mismatch; skipping", mod_name)
            continue
        if len(stock_s_params) != len(stock_w_params):
            logger.warning("EMTR %r Switch param size mismatch; skipping", mod_name)
            continue

        if mod_params == stock_w_params:
            continue

        new_params = bytearray(stock_s_params)
        skipped_chunks = 0
        for j in range(0, len(mod_params) - 3, 4):
            mod_chunk = mod_params[j : j + 4]
            if mod_chunk != stock_w_params[j : j + 4]:
                stock_w_chunk = stock_w_params[j : j + 4]
                stock_s_chunk = stock_s_params[j : j + 4]
                if stock_s_chunk == stock_w_chunk[::-1]:
                    new_params[j : j + 4] = mod_chunk[::-1]
                elif stock_s_chunk == stock_w_chunk:
                    new_params[j : j + 4] = mod_chunk
                else:
                    skipped_chunks += 1

        if skipped_chunks:
            logger.warning(
                "EMTR %r has %d changed chunks with unknown WiiU->Switch mapping; left stock Switch bytes",
                mod_name,
                skipped_chunks,
            )

        if new_params != stock_s_params:
            result[s_param_start : s_param_start + len(new_params)] = new_params
            patched += 1

    if not patched:
        return stock_switch

    out = bytes(result)
    return oead.yaz0.compress(out) if stock_switch[:4] == b"Yaz0" else out
