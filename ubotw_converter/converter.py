#!/usr/bin/env python
from subprocess import run
from os.path import sep, splitext
from glob import glob
from urllib.request import urlopen, urlretrieve
from io import BytesIO
from zipfile import ZipFile
from platform import system 
from json import dumps, loads
from pathlib import Path
from multiprocessing import get_context
from typing import Iterator, Optional, Union
from hashlib import sha1
from functools import lru_cache
import sys
import shutil
import argparse
import re
import traceback
import logging
import logging.config
import xxhash

from bcml.install import open_mod, find_modded_files
from bcml.dev import convert_mod, NO_CONVERT_EXTS
from bcml import util
from .bars_py import bars, bcf_converter
from .bflyt import convert_bflyt
from .bflim_convertor import bntx_dds_injector as bntx
import oead

SCRIPT: Path = Path(__file__).parent

# Import dll libraries
BFRES_DLL = SCRIPT / "dotnet_libs" / "BfresLibrary"

import clr
clr.AddReference(str(BFRES_DLL))
from System.IO import MemoryStream, File
from BfresLibrary import ResFile
from BfresLibrary.PlatformConverters import ConverterHandle

# Supported formats
SUPPORTED = [".sbfres", ".sbitemico", ".hkcl", ".hkrg", ".hkrb", ".shknm2", ".shksc", ".shktmrb", ".bars", ".bfstm", ".bflim", ".bflyt", ".sblarc", ".bcamanim"]

BFRES_EXT = [".sbfres", ".sbitemico", ".bcamanim"]
HAVOK_EXT = [".hkcl", ".hkrg", ".hkrb", ".shknm2", ".shksc", ".shktmrb"]
LAYOUT_EXT = [".bflan", ".bgsh", ".bnsh", ".bushvt", ".bflyt", ".bflim", ".bntx"]
SOUND_EXT = [".bfstm", ".bfstp", ".bfwav", ".bars"]

# Construct an argument parser
parser = argparse.ArgumentParser(description="Converts mods in BNP format using BCML's converter, complemented by some additional tools")
parser.add_argument("bnp", nargs='+')
parser.add_argument("-o", "--output", help="Specify an output file")
parser.add_argument("-s", "--single", help="Use single core", action="store_true")
parser.add_argument("-log", "--log-level", default="warning", help="Set the logging level. Example --log-level debug. Default is warning")
args = parser.parse_args()

LOG_CONF = SCRIPT / "log.conf"
ERROR_LOG = SCRIPT / "error.log"

# Error logging
logging.config.fileConfig(fname=LOG_CONF, defaults={"logfilename": ERROR_LOG, "loglevel": args.log_level.upper()})
logger = logging.getLogger(__name__)

WRAPPER_STATE_FILE = "__wrapper_state__"

# Keep `.sesetlist` in BCML's unsupported set so event-pack extraction still
# falls back to stock Switch payloads for unmodified files. The PTCL post-pass
# below then recompresses those restored raw `VFXB` payloads back into the
# expected Yaz0 wrapper inside event packs.

def is_file_modded(name: str, file: Union[bytes, Path], count_new: bool = True) -> bool:
    table = util.get_hash_table(True)
    if name not in table:
        return count_new
    contents = (
        file
        if isinstance(file, bytes)
        else file.read_bytes()
        if isinstance(file, Path)
        else bytes(file)
    )
    if contents[0:4] == b"Yaz0":
        try:
            contents = util.decompress(contents)
        except RuntimeError as err:
            raise ValueError(f"Invalid yaz0 file {name}") from err
    fhash = xxhash.xxh64_intdigest(contents)
    return not fhash in table[name]

def confirm_prompt(question: str) -> bool:
    # https://gist.github.com/garrettdreyfus/8153571
    reply = None
    while reply not in ("", "y", "n"):
        reply = input(f"{question} (Y/n): ").lower()
    return reply in ("", "y")

def extract_sarc(sarc: oead.Sarc, sarc_path: Path) -> None:
    # Extract the data from a SARC file
    Path(sarc_path).mkdir(parents=True, exist_ok=True)
    wrapper_state = {}
    for file in sarc.get_files():
        data = bytes(file.data)
        wrapper_state[file.name] = data[:4] == b"Yaz0"
        if not Path(sarc_path / file.name).parent.exists():
            Path(sarc_path / file.name).parent.mkdir(parents=True, exist_ok=True)
        Path(sarc_path / file.name).write_bytes(data)
    (Path(sarc_path) / WRAPPER_STATE_FILE).write_text(dumps(wrapper_state), encoding="utf-8")

def write_sarc(sarc: oead.Sarc, sarc_path: Path, sarc_file: Path) -> None:
    # Overwrite the SARC file with the modified files
    new_sarc = oead.SarcWriter(endian=oead.Endianness.Little)
    wrapper_state_path = sarc_path / WRAPPER_STATE_FILE
    wrapper_state = (
        loads(wrapper_state_path.read_text(encoding="utf-8"))
        if wrapper_state_path.exists()
        else {}
    )
    for file in sarc_path.rglob("*"):
        if not file.is_file() or file.name == WRAPPER_STATE_FILE:
            continue
        new_file = file.relative_to(sarc_path).as_posix()
        data = file.read_bytes()
        if wrapper_state.get(new_file) is True and data[:4] != b"Yaz0":
            data = oead.yaz0.compress(data)
        elif wrapper_state.get(new_file) is False and data[:4] == b"Yaz0":
            data = util.unyaz_if_needed(data)
        new_sarc.files[new_file] = data
    if sarc_file.suffix == ".pack":
        sarc_file.write_bytes(new_sarc.write()[1])
    else:
        sarc_file.write_bytes(oead.yaz0.compress(new_sarc.write()[1]))


def _normalize_sesetlist_bytes(data: bytes) -> bytes:
    if data[:4] == b"Yaz0":
        return data
    if data[:4] in {b"EFTB", b"VFXB"}:
        return oead.yaz0.compress(data)
    return data


def _recompress_sesetlist_sarc_bytes(data: bytes, compress_outer: bool) -> bytes:
    sarc = oead.Sarc(util.unyaz_if_needed(data))
    writer = oead.SarcWriter.from_sarc(sarc)
    changed = False

    for file in sarc.get_files():
        file_data = bytes(file.data)
        ext = Path(file.name).suffix
        if ext == ".sesetlist":
            normalized = _normalize_sesetlist_bytes(file_data)
            if normalized != file_data:
                writer.files[file.name] = normalized
                changed = True
        elif ext in {".sbeventpack", ".beventpack", ".sarc", ".pack", ".sblarc"}:
            nested_data = _recompress_sesetlist_sarc_bytes(
                file_data, compress_outer=ext.startswith(".s") and ext != ".sarc"
            )
            if nested_data != file_data:
                writer.files[file.name] = nested_data
                changed = True

    if not changed:
        return data

    out = writer.write()[1]
    return oead.yaz0.compress(out) if compress_outer else out


def _recompress_sesetlists_in_mod(mod_path: Path) -> None:
    for file in mod_path.rglob("*.*"):
        if not file.is_file():
            continue
        ext = file.suffix
        if ext not in {".sbeventpack", ".beventpack"}:
            continue
        new_data = _recompress_sesetlist_sarc_bytes(
            file.read_bytes(), compress_outer=ext.startswith(".s") and ext != ".sarc"
        )
        if new_data != file.read_bytes():
            file.write_bytes(new_data)

def _get_stock_bfres(file: Path, mod_path: Optional[Path], switch_name: str) -> Optional[ResFile]:
    stock_bytes = _get_stock_bfres_bytes(file, mod_path, switch_name)
    if stock_bytes is None:
        return None
    return ResFile(MemoryStream(stock_bytes))


def _get_stock_bfres_bytes(file: Path, mod_path: Optional[Path], switch_name: str) -> Optional[bytes]:
    if not mod_path:
        return None

    for root in ("content", "aoc"):
        base = mod_path / root
        if base in file.parents:
            rel = file.relative_to(base).with_name(switch_name)
            try:
                stock_file = util.get_game_file(rel.as_posix())
                return util.unyaz_if_needed(stock_file.read_bytes())
            except FileNotFoundError:
                return None

    pack_parent = next((parent for parent in file.parents if parent.suffix == ".pack"), None)
    if pack_parent:
        inner_rel = file.relative_to(pack_parent).with_name(switch_name).as_posix()
        try:
            stock_pack = util.get_game_file(f"Pack/{pack_parent.name}")
            stock_sarc = oead.Sarc(util.unyaz_if_needed(stock_pack.read_bytes()))
            stock_inner = stock_sarc.get_file(inner_rel)
            if stock_inner:
                return util.unyaz_if_needed(bytes(stock_inner.data))
        except FileNotFoundError:
            return None
    return None


def _apply_switch_tex_template(template: ResFile, converted: ResFile, name: str) -> Optional[ResFile]:
    template_names = [texture.Name for texture in list(template.Textures.Values)]
    converted_names = {texture.Name for texture in list(converted.Textures.Values)}
    if set(template_names) != converted_names:
        return None

    for texture_name in template_names:
        target = template.Textures[texture_name]
        source = converted.Textures[texture_name]
        target.FromWiiU(source)

    template.Name = name
    return template


def _find_external_tex1(file: Path, root_mod_path: Optional[Path]) -> Optional[Path]:
    if not root_mod_path or ".Tex2" not in file.suffixes:
        return None

    tex1_name = file.name.replace("Tex2", "Tex1")
    for root in ("content", "aoc"):
        candidate = root_mod_path / root / "Model" / tex1_name
        if candidate.exists():
            return candidate

    # Some mods keep matching Tex1 files inside option subfolders instead of
    # directly under the mod root content/Model path.
    for candidate in root_mod_path.rglob(tex1_name):
        if "Model" in candidate.parts and any(part in ("content", "aoc") for part in candidate.parts):
            return candidate
    return None


def _merge_external_tex1_with_tex2(tex1_path: Path, tex2_res_file: ResFile, name: str) -> Optional[ResFile]:
    tex1_res_file = ResFile(MemoryStream(util.unyaz_if_needed(tex1_path.read_bytes())))
    return _merge_tex1_res_file_with_tex2(tex1_res_file, tex2_res_file, name)


def _merge_tex1_res_file_with_tex2(tex1_res_file: ResFile, tex2_res_file: ResFile, name: str) -> Optional[ResFile]:
    tex1_names = {texture.Name for texture in list(tex1_res_file.Textures.Values)}
    tex2_names = {texture.Name for texture in list(tex2_res_file.Textures.Values)}
    if tex1_names != tex2_names:
        return None

    for texture in list(tex2_res_file.Textures.Values):
        target = tex1_res_file.Textures[texture.Name]
        target.MipSwizzle = texture.MipSwizzle
        target.MipData = texture.MipData
        target.MipOffsets = texture.MipOffsets

    tex1_res_file.Name = name
    return tex1_res_file


def _iter_wiiu_content_roots() -> Iterator[Path]:
    for key in ("update_dir", "dlc_dir", "game_dir"):
        try:
            root = util.get_settings(key)
        except Exception:
            continue
        if root:
            yield Path(root)


def _get_stock_wiiu_tex1(file: Path) -> Optional[ResFile]:
    if ".Tex2" not in file.suffixes:
        return None

    tex1_name = file.name.replace("Tex2", "Tex1")
    pack_parent = next((parent for parent in file.parents if parent.suffix == ".pack"), None)
    if pack_parent:
        inner_rel = file.relative_to(pack_parent).with_name(tex1_name).as_posix()
        for root in _iter_wiiu_content_roots():
            pack_path = root / "Pack" / pack_parent.name
            if not pack_path.exists():
                continue
            try:
                stock_sarc = oead.Sarc(util.unyaz_if_needed(pack_path.read_bytes()))
                stock_inner = stock_sarc.get_file(inner_rel)
                if stock_inner:
                    return ResFile(MemoryStream(util.unyaz_if_needed(bytes(stock_inner.data))))
            except Exception:
                continue

    for root in _iter_wiiu_content_roots():
        loose_tex1 = root / "Model" / tex1_name
        if loose_tex1.exists():
            return ResFile(MemoryStream(util.unyaz_if_needed(loose_tex1.read_bytes())))

    return None


def _trim_texture_to_base_mip(texture) -> None:
    texture.MipCount = 1
    texture.MipData = b""
    texture.MipOffsets = [0] * len(list(texture.MipOffsets))


def _collapse_embedded_mips(res_file: ResFile) -> bool:
    collapsed = False
    for texture in list(res_file.Textures.Values):
        if int(texture.MipCount) <= 1 or len(bytes(texture.MipData)) != 0:
            continue
        _trim_texture_to_base_mip(texture)
        collapsed = True
    return collapsed


def _sanitize_invalid_mips(res_file: ResFile) -> bool:
    sanitized = False
    for texture in list(res_file.Textures.Values):
        bad_mip = None
        for mip in range(int(texture.MipCount)):
            try:
                texture.GetDeswizzledData(0, mip)
            except Exception:
                bad_mip = mip
                break

        if bad_mip is None:
            continue
        if bad_mip <= 0:
            raise ValueError(f"Texture {texture.Name} has invalid base mip data")

        texture.MipCount = bad_mip
        texture.MipData = b""
        texture.MipOffsets = [0] * len(list(texture.MipOffsets))
        sanitized = True
    return sanitized


def _collapse_all_texture_mips(res_file: ResFile) -> bool:
    collapsed = False
    for texture in list(res_file.Textures.Values):
        if int(texture.MipCount) <= 1 and len(bytes(texture.MipData)) == 0:
            continue
        _trim_texture_to_base_mip(texture)
        collapsed = True
    return collapsed


def _get_temp_extract_root(file: Path) -> Path:
    for parent in (file.parent, *file.parents):
        if parent.name.startswith("_tmp_extract_"):
            return parent

    mod_label = "mod"
    mod_root = None
    for parent in (file.parent, *file.parents):
        info = parent / "info.json"
        if info.exists():
            mod_root = parent
            try:
                meta = loads(info.read_text("utf-8"))
                mod_label = meta.get("name") or parent.name
            except Exception:
                mod_label = parent.name
            break

    if mod_root is None:
        mod_root = file.parent
        mod_label = file.parent.name or "mod"

    safe_mod_name = re.sub(r"[^A-Za-z0-9._-]+", "_", mod_label).strip("._-") or "mod"
    mod_digest = sha1(str(mod_root).encode("utf-8")).hexdigest()[:8]
    return SCRIPT / f"_tmp_extract_{safe_mod_name}_{mod_digest}"


def _get_temp_extract_path(file: Path) -> Path:
    temp_root = _get_temp_extract_root(file)
    file_digest = sha1(str(file).encode("utf-8")).hexdigest()[:12]
    return temp_root / file_digest / file.name


def _format_conversion_target(file: Path, mod_path: Path) -> str:
    rel = file.relative_to(mod_path)
    if any(part.startswith("_tmp_extract") for part in mod_path.parts) and mod_path.suffix:
        return f"{mod_path.name} -> {rel}"
    return str(rel)


def _cleanup_temp_extract_path(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
    try:
        path.parent.rmdir()
    except OSError:
        pass


def _find_section_offsets(data: bytes, magic: bytes) -> list[int]:
    offsets = []
    start = 0
    while True:
        index = data.find(magic, start)
        if index == -1:
            break
        offsets.append(index)
        start = index + 4
    return offsets


def _repair_switch_visibility_bytes(data: bytearray, stock_bytes: Optional[bytes]) -> None:
    if not stock_bytes:
        return

    stock_offsets = _find_section_offsets(stock_bytes, b"FBVS")
    if not stock_offsets:
        return

    output_offsets = _find_section_offsets(data, b"FVIS")
    if not output_offsets:
        output_offsets = _find_section_offsets(data, b"FBVS")
    if not output_offsets:
        return

    for stock_offset, output_offset in zip(stock_offsets, output_offsets):
        block_ptr = int.from_bytes(stock_bytes[stock_offset + 0x38 : stock_offset + 0x40], "little")
        if not block_ptr or block_ptr + 0x20 > len(stock_bytes):
            continue

        block = stock_bytes[block_ptr : block_ptr + 0x20]
        new_offset = (len(data) + 7) & ~7
        if new_offset > len(data):
            data.extend(b"\x00" * (new_offset - len(data)))
        data.extend(block)

        data[output_offset + 0x38 : output_offset + 0x40] = new_offset.to_bytes(8, "little")
        data[output_offset + 0x48 : output_offset + 0x50] = (new_offset + 8).to_bytes(8, "little")
        data[output_offset + 0x60 : output_offset + 0x68] = stock_bytes[stock_offset + 0x60 : stock_offset + 0x68]


def _patch_switch_bfres_bytes(data: bytes, res_file: ResFile, stock_bytes: Optional[bytes] = None) -> bytes:
    try:
        bone_vis_count = len(list(res_file.BoneVisibilityAnims.Values))
    except Exception:
        bone_vis_count = 0

    if not bone_vis_count:
        return data

    patched = bytearray(data)

    _repair_switch_visibility_bytes(patched, stock_bytes)

    if b"FVIS" in patched:
        start = 0
        replaced = 0
        while replaced < bone_vis_count:
            index = patched.find(b"FVIS", start)
            if index == -1:
                break
            patched[index : index + 4] = b"FBVS"
            replaced += 1
            start = index + 4
    return bytes(patched)


def _validate_switch_bfres_bytes(data: bytes, name: str) -> None:
    if str(args.log_level).lower() != "debug":
        return
    try:
        ResFile(MemoryStream(data))
    except Exception as err:
        raise ValueError(f"Converted BFRES is invalid for {name}") from err


def convert_bfres(sbfres: Path, mod_path: Optional[Path] = None, root_mod_path: Optional[Path] = None) -> None:
    # Based on https://github.com/KillzXGaming/BfresPlatformConverter
    name: str = sbfres.stem
    ext: str = sbfres.suffix
    is_tex1 = ".Tex1" in sbfres.suffixes
    is_tex2 = ".Tex2" in sbfres.suffixes
    standalone_tex2 = is_tex2 and not Path(str(sbfres).replace("Tex2", "Tex1")).exists()

    bfres: bytes = util.unyaz_if_needed(sbfres.read_bytes())

    res_file: ResFile = ResFile(MemoryStream(bfres))
    tex2: Optional[Path] = None

    if is_tex1 and max({i.MipCount for i in list(res_file.Textures.Values)}) > 1:
        tex2 = Path(str(sbfres).replace("Tex1", "Tex2"))
        if tex2.exists():
            res_file_tex2 = ResFile(MemoryStream(util.unyaz_if_needed(tex2.read_bytes())))
            for texture in list(res_file_tex2.Textures.Values):
                target = res_file.Textures[texture.Name]
                target.MipSwizzle = texture.MipSwizzle
                target.MipData = texture.MipData
                target.MipOffsets = texture.MipOffsets
        else:
            _collapse_embedded_mips(res_file)

        name = name.replace("Tex1", "Tex")
        res_file.Name = name
    elif standalone_tex2:
        name = name.replace("Tex2", "Tex")
        res_file.Name = name
        external_tex1 = _find_external_tex1(sbfres, root_mod_path)
        merged = None
        if external_tex1:
            merged = _merge_external_tex1_with_tex2(external_tex1, res_file, name)
        else:
            stock_wiiu_tex1 = _get_stock_wiiu_tex1(sbfres)
            if stock_wiiu_tex1:
                merged = _merge_tex1_res_file_with_tex2(stock_wiiu_tex1, res_file, name)
        if merged:
            res_file = merged
    
    if not res_file.IsPlatformSwitch:
        stock_bytes = _get_stock_bfres_bytes(sbfres, mod_path, f"{name}{ext}")

        if standalone_tex2 and stock_bytes:
            stock_tex = ResFile(MemoryStream(stock_bytes))
            templated = _apply_switch_tex_template(stock_tex, res_file, name)
            if templated:
                mem = MemoryStream()
                templated.Save(mem)
                saved_bytes = _patch_switch_bfres_bytes(bytes(mem.ToArray()), templated, stock_bytes)
                _validate_switch_bfres_bytes(saved_bytes, sbfres.name)
                if sbfres.suffix.startswith(".s"):
                    sbfres.write_bytes(oead.yaz0.compress(saved_bytes))
                else:
                    sbfres.write_bytes(saved_bytes)
                sbfres.rename(sbfres.with_name(f'{name}{ext}'))
                return

        try:
            res_file.ChangePlatform(True, 4096, 5, 0, 0, 3, ConverterHandle.BOTW)
        except Exception as first_err:
            sanitized = False
            try:
                sanitized = _sanitize_invalid_mips(res_file)
            except Exception:
                sanitized = False

            if sanitized:
                try:
                    res_file.ChangePlatform(True, 4096, 5, 0, 0, 3, ConverterHandle.BOTW)
                except Exception as second_err:
                    if not _collapse_all_texture_mips(res_file):
                        raise second_err
                    res_file.ChangePlatform(True, 4096, 5, 0, 0, 3, ConverterHandle.BOTW)
            else:
                if not _collapse_all_texture_mips(res_file):
                    raise first_err
                res_file.ChangePlatform(True, 4096, 5, 0, 0, 3, ConverterHandle.BOTW)
        res_file.Alignment = 0x08 if sbfres.suffix == ".bcamanim" else 0x0C
        output_res_file = res_file

        if standalone_tex2:
            stock_tex = ResFile(MemoryStream(stock_bytes)) if stock_bytes else None
            templated = _apply_switch_tex_template(stock_tex, res_file, name) if stock_tex else None
            if templated:
                output_res_file = templated

        if sbfres.suffix.startswith(".s"):
            mem = MemoryStream()
            output_res_file.Save(mem)
            saved_bytes = _patch_switch_bfres_bytes(bytes(mem.ToArray()), output_res_file, stock_bytes)
            _validate_switch_bfres_bytes(saved_bytes, sbfres.name)
            sbfres.write_bytes(oead.yaz0.compress(saved_bytes))
        else:
            mem = MemoryStream()
            output_res_file.Save(mem)
            saved_bytes = _patch_switch_bfres_bytes(bytes(mem.ToArray()), output_res_file, stock_bytes)
            _validate_switch_bfres_bytes(saved_bytes, sbfres.name)
            sbfres.write_bytes(saved_bytes)
        
        if is_tex1 and tex2 and tex2.exists():
            tex2.unlink()
            
        if is_tex1 or standalone_tex2:
            sbfres.rename(sbfres.with_name(f'{name}{ext}'))

def convert_havok(hkx: Path) -> None:
    # Convert havok files unsupported by BCML
    readwrite = SCRIPT / "ReadWrite.exe" if system() == "Windows" else None
    print(f"Converting {hkx.name}")
    compress_back = hkx.suffix.startswith(".s")
    raw_suffix = f".{hkx.suffix[2:]}" if compress_back else hkx.suffix
    work_hkx = hkx.with_suffix(raw_suffix) if raw_suffix != hkx.suffix else hkx
    out_hkx = Path(f"{work_hkx}.out")
    raw_bytes = util.unyaz_if_needed(hkx.read_bytes()) if compress_back else hkx.read_bytes()

    if not readwrite or not readwrite.exists():
        raise FileNotFoundError(f"ReadWrite.exe not found: {readwrite}")

    readwrite.chmod(0o755)
    work_hkx.write_bytes(raw_bytes)
    try:
        run([str(readwrite), str(work_hkx)])
        out_bytes = out_hkx.read_bytes()
        hkx.write_bytes(oead.yaz0.compress(out_bytes) if compress_back else out_bytes)
    finally:
        if out_hkx.exists():
            out_hkx.unlink()
        if work_hkx != hkx and work_hkx.exists():
            work_hkx.unlink()

def get_stock_bfstp(bfstp_name: str, bars_file: Path):
    # Look for the bars file containing the bfstp
    try:
        stock_bars = util.get_game_file(f"Sound/Resource/{bars_file.name}")
        stock_tracks,_ = bars.get_bars_tracks(stock_bars.read_bytes())
    except FileNotFoundError:
        try:
            # If there's no loose bars file, first try regular packs.
            stock_pack = util.get_game_file(f'Pack/{bars_file.parent.parent.parent.name}')
            stock_bars = oead.Sarc(stock_pack.read_bytes()).get_file(f"Sound/Resource/{bars_file.name}")
            stock_tracks, stock_offsets = bars.get_bars_tracks(bytearray(stock_bars.data))
        except FileNotFoundError:
            try:
                # Then try an event pack whose name matches the current context.
                stock_pack = util.get_game_file(f'Event/{bars_file.parent.parent.parent.name}')
                stock_bars = oead.Sarc(util.unyaz_if_needed(stock_pack.read_bytes())).get_file(f"Sound/Resource/{bars_file.name}")
                if not isinstance(stock_bars, oead.File):
                    raise FileNotFoundError(f"File Sound/Resource/{bars_file.name} was not found in game dump.")
                stock_tracks, stock_offsets = bars.get_bars_tracks(bytearray(stock_bars.data))
            except FileNotFoundError:
                # Loose event bars under content/Sound/Resource have no useful
                # pack-name context, so search stock event packs once and use
                # a cached index for repeated lookups.
                stock_event = _get_stock_event_bars_index().get(bars_file.name)
                if stock_event is None:
                    raise FileNotFoundError(f"File Sound/Resource/{bars_file.name} was not found in stock event packs.")
                stock_pack = oead.Sarc(util.unyaz_if_needed(stock_event.read_bytes()))
                stock_bars = stock_pack.get_file(f"Sound/Resource/{bars_file.name}")
                if not isinstance(stock_bars, oead.File):
                    raise FileNotFoundError(f"File Sound/Resource/{bars_file.name} was not found in stock event pack {stock_event.name}.")
                stock_tracks, _ = bars.get_bars_tracks(bytearray(stock_bars.data))
    return stock_tracks[bfstp_name]


@lru_cache(maxsize=1)
def _get_stock_event_bars_index():
    event_root = Path(util.get_game_file("Pack/Bootup.pack")).parent.parent / "Event"
    bars_index = {}
    for pattern in ("*.sbeventpack", "*.beventpack"):
        for stock_event in event_root.glob(pattern):
            try:
                stock_pack = oead.Sarc(util.unyaz_if_needed(stock_event.read_bytes()))
                for file in stock_pack.get_files():
                    if file.name.startswith("Sound/Resource/") and file.name.endswith(".bars"):
                        bars_index.setdefault(file.name.rsplit("/", 1)[-1], stock_event)
            except Exception:
                continue
    return bars_index

def convert_bflim(sblarc: Path, pack_name: str) -> None:
    # Convert bflim files inside a WiiU sblarc
    blarc = oead.Sarc(util.unyaz_if_needed(sblarc.read_bytes()))
    blarc_path = _get_temp_extract_path(sblarc)
    stock_blarc = None

    if any("bflim" in i.name for i in blarc.get_files()):
        # Get the pack file where the sblarc comes from
        stock_pack = util.get_game_file(f"Pack/{pack_name}")

        if pack_name == "Bootup.pack":
            # If the sblarc is in Bootup.pack, get a stock Common.sblarc
            stock_sblarc = oead.Sarc(stock_pack.read_bytes()).get_file("Layout/Common.sblarc")
            stock_blarc = oead.Sarc(util.unyaz_if_needed(stock_sblarc.data))

        elif pack_name == "Title.pack":
            # If the sblarc is in Title.pack, get a stock Title.sblarc
            stock_sblarc = oead.Sarc(stock_pack.read_bytes()).get_file("Layout/Title.sblarc")
            stock_blarc = oead.Sarc(util.unyaz_if_needed(stock_sblarc.data))

        # Get a stock bntx file
        bntx_file = stock_blarc.get_file("timg/__Combined.bntx")
        if stock_blarc:
            # For stock system UI archives, keep the stock Switch layout as the
            # base and only inject converted textures into the combined BNTX.
            extract_sarc(stock_blarc, blarc_path)
        else:
            extract_sarc(blarc, blarc_path)
        Path(blarc_path / bntx_file.name).write_bytes(bntx_file.data)

        for bflim in blarc_path.rglob('*.bflim'):
            try:
                # Inject every bflim found into the bntx file
                bntx.tex_inject(blarc_path / bntx_file.name, bflim)
                Path(bflim).unlink()
            except Exception as err:
                logging.warning(f"{bflim.relative_to(blarc_path)} could not be converted")
                logging.debug(err, exc_info=True)
        # Write the new blarc file
        write_sarc(blarc, blarc_path, sblarc)

        # Remove the temporary folder
        _cleanup_temp_extract_path(blarc_path)

def convert_bflyt_sblarc(sblarc: Path) -> None:
    # Convert bflyt files inside a WiiU sblarc
    blarc = oead.Sarc(util.unyaz_if_needed(sblarc.read_bytes()))
    blarc_path = _get_temp_extract_path(sblarc)

    if any("bflyt" in i.name for i in blarc.get_files()):
        extract_sarc(blarc, blarc_path)
        try:
            for bflyt in blarc_path.rglob('*.bflyt'):
                try:
                    convert_bflyt(bflyt)
                except Exception as err:
                    logging.warning(f"{bflyt.relative_to(blarc_path)} could not be converted")
                    logging.debug(err, exc_info=True)

            write_sarc(blarc, blarc_path, sblarc)
        finally:
            _cleanup_temp_extract_path(blarc_path)

def change_platform(file: Path, mod_path: Path, root_mod_path: Path = None) -> None:
    if file.suffix in BFRES_EXT:
        # Convert FRES files
        if ".Tex2" not in file.suffixes or not Path(str(file).replace("Tex2", "Tex1")).exists():
            convert_bfres(file, mod_path, root_mod_path)

    elif file.suffix == ".bars":
        # Convert bars files
        bars_bytes = bytearray(file.read_bytes())
        tracks, offsets = bars.get_bars_tracks(bars_bytes)
        for name, data in tracks.items():
            # Read the track header and convert appropiately
            magic: str = data[:0x4].decode("utf-8")
            try:
                try:
                    bfstm_exists = next(mod_path.rglob(name + ".bfstm"))
                except StopIteration:
                    bfstm_exists = next(root_mod_path.rglob(name + ".bfstm"))
            except:
                bfstm_exists = None

            if magic == 'FWAV':
                tracks[name] = bcf_converter.conv_file(data, magic, '<')

            elif magic == 'FSTP' and bfstm_exists:
                tracks[name] = bcf_converter.conv_file(data, magic, '<')

            elif magic == 'FSTP' and not bfstm_exists:
                try:
                    tracks[name] = get_stock_bfstp(name, file)
                except FileNotFoundError:
                    # Some mods add or replace embedded FSTP tracks that have
                    # no stock Switch counterpart. In those cases we still can
                    # byte-convert the embedded stream directly.
                    tracks[name] = bcf_converter.conv_file(data, magic, '<')

            bars_bytes[offsets[name]:offsets[name] + len(tracks[name])] = tracks[name]

        new_bars = bars.convert_bars(bars_bytes, '<')
        file.write_bytes(bytes(new_bars))
        print("Successfully converted " + file.name + "!")

    elif file.suffix == ".bfstm":
        # Convert BFSTM files
        new_bfstm = bcf_converter.conv_file(file.read_bytes(), "FSTM", '<')
        file.write_bytes(bytes(new_bfstm))
        print("Successfully converted " + file.name + "!")

    elif file.suffix == ".bflyt":
        # Convert layout files
        convert_bflyt(file)
        print("Successfully converted " + file.name + "!")

    elif "pack" in file.suffix and file.suffix != ".sbquestpack":
        # Convert files inside of pack files
        pack = oead.Sarc(util.unyaz_if_needed(file.read_bytes()))
        pack_path = _get_temp_extract_path(file)
        if any(splitext(i.name)[1] in SUPPORTED for i in pack.get_files()):
            try:
                if file.name in {"Bootup.pack", "Title.pack"}:
                    stock_pack = oead.Sarc(util.unyaz_if_needed(util.get_game_file(f"Pack/{file.name}").read_bytes()))
                    extract_sarc(stock_pack, pack_path)
                extract_sarc(pack, pack_path)
                new_files = pack_path.rglob('*.*')
                for new in new_files:
                    try:
                        convert_files(new, pack_path, root_mod_path or mod_path)
                    except Exception as err:
                        logger.warning(f"{new.relative_to(pack_path)} could not be converted")
                        logger.debug(err, exc_info=True)
                write_sarc(pack, pack_path, file)
                
            finally:
                _cleanup_temp_extract_path(pack_path)

    elif file.suffix == ".sblarc":
        if file.name == "BootUp.sblarc":
            logging.warning("A BootUp.sblarc was found! These files are not used on Switch, so it was skipped")
            file.unlink()
        else:
            # Convert bflim files inside of sblarc files
            convert_bflim(file, mod_path.name)
            if not (
                (mod_path.name == "Bootup.pack" and file.name == "Common.sblarc")
                or (mod_path.name == "Title.pack" and file.name == "Title.sblarc")
            ):
                # Convert bflyt files inside of sblarc files
                convert_bflyt_sblarc(file)

    elif file.suffix in HAVOK_EXT:
        # Convert havok files
        convert_havok(file)

def convert_files(file: Path, mod_path: Path, root_mod_path = None) -> None:
    if not file.exists() or file.stat().st_size == 0:
        return

    if file.suffix in BFRES_EXT:
        try:
            if ".Tex2" in file.suffixes and Path(str(file).replace("Tex2", "Tex1")).exists():
                return
            change_platform(file, mod_path, root_mod_path)
        except Exception as err:
            logger.warning(f"{_format_conversion_target(file, mod_path)} could not be converted")
            logger.debug(err, exc_info=True)
        return

    try:
        canon = util.get_canon_name(file.relative_to(mod_path), allow_no_source=True)
        is_modded = is_file_modded(canon, file.read_bytes())

        # Convert supported files
        if is_modded or file.suffix == ".bars": 
            change_platform(file, mod_path, root_mod_path)
            
        elif file.suffix in NO_CONVERT_EXTS or file.suffix == ".bcamanim":
            content_root = mod_path / "content"
            if mod_path.parent != SCRIPT and content_root.exists() and file.is_relative_to(content_root):
                stock_file = util.get_game_file(file.relative_to(content_root))
                file.write_bytes(stock_file.read_bytes())
            # TODO: Add logic for stock files inside modified packs
            elif "pack" in mod_path.suffix and mod_path.suffix != ".sbquestpack":
                try:
                    stock_pack = util.get_game_file(f"Actor/Pack/{mod_path.name}")
                except FileNotFoundError:
                    try:
                        stock_pack = util.get_game_file(f"Event/{mod_path.name}")
                    except FileNotFoundError:
                        try:
                            stock_pack = util.get_game_file(f"Pack/{mod_path.name}")
                        except FileNotFoundError:
                            try:
                                stock_pack = util.get_game_file(f"Actor/Pack/{file.name.split('.')[0].replace('_A', '')}.sbactorpack")
                            except FileNotFoundError:
                                try:
                                    stock_pack = util.get_game_file(f"Event/{file.name.split('.')[0].replace('Event_', '').replace('_Open', '_0')}.sbeventpack")
                                except FileNotFoundError:
                                    change_platform(file, mod_path)

                if 'stock_pack' in locals():
                    try:
                        stock_file = util.get_nested_file_bytes(f"{stock_pack}//{file.relative_to(mod_path).as_posix()}")
                        file.write_bytes(stock_file)
                    except:
                        change_platform(file, mod_path)
                
    except Exception as err:
        logger.warning(f"{_format_conversion_target(file, mod_path)} could not be converted")
        logger.debug(err, exc_info=True)

def convert(mod: Path) -> None:
    # Open the mod
    mod_path = open_mod(mod)
    temp_extract_root = _get_temp_extract_root(mod_path / "info.json")
    try:
        if (mod_path / "info.json").exists():
            meta = loads((mod_path / "info.json").read_text("utf-8"))

        if meta["platform"] == "switch":
            raise NotImplementedError("Ultimate BotW Converter does not support Switch to Wii U conversion")

        pack_files = []
        other_files = []
        for file in mod_path.rglob("*.*"):
            if "content" in file.parts or "aoc" in file.parts:
                task = (file, mod_path, mod_path)
                if "pack" in file.suffix or file.suffix == ".sblarc":
                    pack_files.append(task)
                else:
                    other_files.append(task)

        phases = [pack_files, other_files]

        # Convert supported files
        with util.TempSettingsContext({"wiiu": False}):
            for files in phases:
                if not files:
                    continue
                if not args.single:
                    with get_context("spawn").Pool(maxtasksperchild=500) as pool:
                        pool.starmap(convert_files, files)
                        pool.close()
                        pool.join()
                else:
                    for file, _, root_mod_path in files:
                        convert_files(file, mod_path, root_mod_path)
        
        # Run the mod through BCML's automatic converter 
        warnings = convert_mod(mod_path, False, True)
        _recompress_sesetlists_in_mod(mod_path)

        # Pack the converted mod into a new bnp
        out = Path(f'{args.output}.bnp') if args.output else mod.with_name(f"{mod.stem}_switch.bnp")
        if Path(out).exists():
            Path(out).unlink()

        x_args = [
            util.get_7z_path(),
            "a",
            str(out),
            f'{str(mod_path / "*")}',
        ]
        run(x_args)

        # Write BCML's warning to a file
        if warnings:
            with open(ERROR_LOG, "a", encoding="utf-8") as file:
                for warning in warnings:
                    # Write BCML's warning to a file    
                    if all(i not in warning for i in SUPPORTED):
                        if warning.startswith("This mod contains a file not supported by the converter: "):
                            warning = warning.replace(
                                "This mod contains a file not supported by the converter: ",
                                "BCML marked this file as unsupported via NO_CONVERT_EXTS: ",
                                1,
                            )
                        logger.warning(warning)

    except Exception as err:
        print(traceback.format_exc())

    finally:
        # Remove the temporary mod_path
        shutil.rmtree(mod_path, ignore_errors=True)
        shutil.rmtree(temp_extract_root, ignore_errors=True)

def main() -> None:
    ERROR_LOG.write_text("", encoding="utf-8")

    if len(args.bnp) == 1: # one argument
        arg_path = Path(args.bnp[0])
        if arg_path.exists():
            mods = [str(arg_path)]
        else:
            try:
                mods = glob(args.bnp[0])
            except re.error:
                mods = []
            if not mods:
                raise FileNotFoundError(f"Could not find BNP file: {args.bnp[0]}")
    else: # more than one argument
    	mods = args.bnp
    
    for mod in mods:
        convert(Path(mod))

    if ERROR_LOG.stat().st_size != 0:
        print(f"It seems some files could not be converted. Please check the error log at {ERROR_LOG} for more info.")


if __name__ == "__main__":
    main()
