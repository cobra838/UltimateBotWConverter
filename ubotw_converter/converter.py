#!/usr/bin/env python
from subprocess import run
from os.path import sep
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
from .bflim_convertor import bntx_dds_injector as bntx
from .sbeco import convert_to_little_endian as convert_sbeco_bytes
from .ptcl import convert_sesetlist
import oead

SCRIPT: Path = Path(__file__).parent
LAYOUT_EXPORTER_U_DIR = SCRIPT / "LayoutExporterU"

def convert_bflan_layoutu(file: Path) -> None:
    path = str(LAYOUT_EXPORTER_U_DIR)
    if path not in sys.path:
        sys.path.insert(0, path)
    import bflan as layoutu_bflan

    layoutu_bflan.toVersion(file.read_bytes(), str(file), 0x08000000)


def convert_bflyt_layoutu(file: Path) -> None:
    path = str(LAYOUT_EXPORTER_U_DIR)
    if path not in sys.path:
        sys.path.insert(0, path)
    import bflyt as layoutu_bflyt

    layoutu_bflyt.toVersion(file.read_bytes(), str(file), 0x08000000)


# Import dll libraries
BFRES_DLL = SCRIPT / "dotnet_libs" / "BfresLibrary"

import clr
clr.AddReference(str(BFRES_DLL))
from System.IO import MemoryStream, File
from BfresLibrary import ResFile
from BfresLibrary.PlatformConverters import ConverterHandle

# Supported formats
SUPPORTED = [".sbfres", ".sbitemico", ".hkcl", ".hkrg", ".hkrb", ".shknm2", ".shksc", ".shktmrb", ".bars", ".bfstm", ".bflim", ".bflyt", ".sblarc", ".bcamanim", ".sbeco"]
COMPATIBLE_EXT = [".bfevfl", ".sblwp", ".fxparam", ".jpg", ".txt", ".json"]

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
TEXTURE_LOG = SCRIPT / "texture.log"

# Error logging
logging.config.fileConfig(fname=LOG_CONF, defaults={"logfilename": ERROR_LOG, "loglevel": args.log_level.upper()})
logger = logging.getLogger(__name__)

WRAPPER_STATE_FILE = "__wrapper_state__"
SOURCE_PATH_FILE = "__source_path__"
CONSUMED_TEX_DIR = "__consumed_tex__"

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

def extract_sarc(sarc: oead.Sarc, sarc_path: Path, source_path: Optional[Path] = None) -> None:
    # Extract the data from a SARC file
    Path(sarc_path).mkdir(parents=True, exist_ok=True)
    wrapper_state = {}
    for file in sarc.get_files():
        data = bytes(file.data)
        wrapper_state[file.name] = data[:4] == b"Yaz0"
        safe_name = file.name.lstrip("/\\")
        if not Path(sarc_path / safe_name).parent.exists():
            Path(sarc_path / safe_name).parent.mkdir(parents=True, exist_ok=True)
        Path(sarc_path / safe_name).write_bytes(data)
    (Path(sarc_path) / WRAPPER_STATE_FILE).write_text(dumps(wrapper_state), encoding="utf-8")
    if source_path is not None:
        (Path(sarc_path) / SOURCE_PATH_FILE).write_text(str(source_path), encoding="utf-8")

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
        if not file.is_file() or file.name in {WRAPPER_STATE_FILE, SOURCE_PATH_FILE}:
            continue
        new_file = file.relative_to(sarc_path).as_posix()
        if f"/{new_file}" in wrapper_state:
            new_file = f"/{new_file}"
        data = file.read_bytes()
        if wrapper_state.get(new_file) is True and data[:4] != b"Yaz0":
            data = oead.yaz0.compress(data)
        elif wrapper_state.get(new_file) is False and data[:4] == b"Yaz0":
            data = util.unyaz_if_needed(data)
        new_sarc.files[new_file] = data
    sarc_data = new_sarc.write()[1]
    if sarc_file.read_bytes()[:4] == b"Yaz0":
        sarc_file.write_bytes(oead.yaz0.compress(sarc_data))
    else:
        sarc_file.write_bytes(sarc_data)


def _set_wrapper_state(sarc_path: Path, inner_name: str, is_yaz0: bool) -> None:
    wrapper_state_path = sarc_path / WRAPPER_STATE_FILE
    if not wrapper_state_path.exists():
        return
    wrapper_state = loads(wrapper_state_path.read_text(encoding="utf-8"))
    if f"/{inner_name}" in wrapper_state:
        inner_name = f"/{inner_name}"
    wrapper_state[inner_name] = is_yaz0
    wrapper_state_path.write_text(dumps(wrapper_state), encoding="utf-8")


def _unwrap_yaz0_for_magic(data: bytes) -> bytes:
    if data[:4] != b"Yaz0":
        return data
    return util.unyaz_if_needed(data)


def _get_inner_magic(data: bytes) -> bytes:
    try:
        return _unwrap_yaz0_for_magic(data)[:4]
    except Exception:
        return data[:4]


def _detect_content_format(data: bytes) -> Optional[str]:
    try:
        inner = _unwrap_yaz0_for_magic(data)
    except Exception:
        inner = data

    magic = inner[:4]
    if inner[:8] in (b"\x57\xe0\xe0\x57\x10\xc0\xc0\x10",):
        return "havok"
    if magic == b"FRES":
        return "bfres"
    if magic == b"SARC":
        return "sarc"
    if magic == b"BFEV":
        return ".bfevfl"
    if magic == b"<?xm":
        return "xml"
    # if magic == b"XLNK":
    #     return "xlink"
    # if magic == b"AGST":
    #     return "bagst"
    # if magic == b"Gfx2":
    #     return "layout_shader"
    if magic in (b"AAMP", b"\x00\x00\x00\x04"):
        return "aamp"
    if magic == b"BARS":
        return "bars"
    if magic in (b"FSTM", b"CSTM"):
        return "bfstm"
    if magic == b"FLYT":
        return "bflyt"
    if magic == b"FLAN":
        return "bflan"
    if magic[:2] in (b"BY", b"YB"):
        try:
            oead.byml.from_binary(inner)
            return "byml"
        except Exception:
            return None
    if magic in (b"\x00\x11\x22\x33", b"\x33\x22\x11\x00"):
        try:
            convert_sbeco_bytes(inner)
            return "beco"
        except Exception:
            return None
    return None


def _should_convert_by_content(data: bytes) -> bool:
    content_format = _detect_content_format(data)
    return (
        content_format is not None
        and content_format != "aamp"
        and content_format != "xml"
        and content_format not in COMPATIBLE_EXT
    )


def _get_game_rel(file: Path, root: Path) -> Optional[Path]:
    for folder in ("content", "aoc"):
        base = root / folder
        try:
            return file.relative_to(base)
        except ValueError:
            continue
    return None


def _read_stock_pack_pair(root: Path, source_path: Path) -> tuple[Path, Path, Path]:
    pack_rel = _get_game_rel(source_path, root)
    if pack_rel is None:
        raise FileNotFoundError(f"Unable to resolve stock pack for {source_path}")
    return pack_rel, _get_wiiu_game_file(pack_rel), util.get_game_file(pack_rel)


def _get_wiiu_game_file(rel) -> Path:
    """Return Path to a WiiU stock file without using TempSettingsContext."""
    settings = util.get_settings()
    for key in ("update_dir", "game_dir"):
        base = settings.get(key)
        if base:
            p = Path(base) / rel
            if p.exists():
                return p
    raise FileNotFoundError(f"WiiU stock file not found: {rel}")


def _get_sesetlist_from_pack(pack_path: Path, name: str) -> bytes:
    data = pack_path.read_bytes()
    if data[:4] == b"Yaz0":
        data = oead.yaz0.decompress(data)
    for f in oead.Sarc(data).get_files():
        if f.name == name:
            return bytes(f.data)
    raise FileNotFoundError(f"{name} not found in {pack_path.name}")


def _get_stock_sesetlist_pair(root: Path, source_pack: Path, name: str) -> tuple[bytes, bytes]:
    candidates = []
    try:
        _, stock_wu_pack, stock_sw_pack = _read_stock_pack_pair(root, source_pack)
        candidates.append((stock_wu_pack, stock_sw_pack))
    except FileNotFoundError:
        pass

    inner_stem = Path(name).stem
    if inner_stem.startswith("Event_"):
        event_name = inner_stem.replace("Event_", "", 1).replace("_Open", "_0")
        event_rel = Path("Event") / f"{event_name}.sbeventpack"
        try:
            candidates.append((_get_wiiu_game_file(event_rel), util.get_game_file(event_rel)))
        except FileNotFoundError:
            pass

    for stock_wu_pack, stock_sw_pack in candidates:
        try:
            return (
                _get_sesetlist_from_pack(stock_wu_pack, name),
                _get_sesetlist_from_pack(stock_sw_pack, name),
            )
        except FileNotFoundError:
            continue

    raise FileNotFoundError(f"Stock sesetlist base not found for {name}")


def _remove_dummy_byml_placeholders(mod_path: Path) -> None:
    for file in mod_path.rglob("dummy.byml"):
        if file.is_file():
            file.unlink()


def _get_scope_root(file: Path, root_mod_path: Optional[Path]) -> Optional[Path]:
    if not root_mod_path:
        return None
    try:
        rel = file.relative_to(root_mod_path)
    except ValueError:
        return root_mod_path

    for index, part in enumerate(rel.parts):
        if part in ("content", "aoc"):
            return root_mod_path / Path(*rel.parts[:index]) if index else root_mod_path
    return root_mod_path


def _get_extracted_source_path(file: Path) -> Optional[Path]:
    for parent in (file.parent, *file.parents):
        source_meta = parent / SOURCE_PATH_FILE
        if source_meta.exists():
            try:
                return Path(source_meta.read_text(encoding="utf-8").strip())
            except Exception:
                return None
        if parent.name.startswith("_tmp_extract_"):
            break
    return None


def _find_scoped_model_files(scope_root: Path, name: str) -> list[Path]:
    if not scope_root or not scope_root.exists():
        return []

    candidates = []
    for candidate in scope_root.rglob(name):
        if (
            candidate.is_file()
            and "Model" in candidate.parts
            and any(part in ("content", "aoc") for part in candidate.parts)
        ):
            candidates.append(candidate)

    def rank(path: Path):
        rel = path.relative_to(scope_root).parts
        if len(rel) >= 3 and rel[0] == "content" and rel[1] == "Model":
            group = 0
        elif len(rel) >= 4 and rel[0] == "aoc" and rel[2] == "Model":
            group = 1
        elif "content" in rel:
            group = 2
        elif "aoc" in rel:
            group = 3
        else:
            group = 4
        return (group, len(rel), path.as_posix())

    return sorted(candidates, key=rank)


def _find_scoped_game_files(scope_root: Path, name: str) -> list[Path]:
    if not scope_root or not scope_root.exists():
        return []

    candidates = []
    for candidate in scope_root.rglob(name):
        if (
            candidate.is_file()
            and any(part in ("content", "aoc") for part in candidate.parts)
        ):
            candidates.append(candidate)

    def rank(path: Path):
        rel = path.relative_to(scope_root).parts
        if len(rel) >= 2 and rel[0] == "content":
            group = 0
        elif len(rel) >= 3 and rel[0] == "aoc":
            group = 1
        else:
            group = 2
        return (group, len(rel), path.as_posix())

    return sorted(candidates, key=rank)


def _find_base_model_files(root_mod_path: Path, name: str) -> list[Path]:
    if not root_mod_path or not root_mod_path.exists():
        return []

    model_roots = [root_mod_path / "content" / "Model"]
    aoc_root = root_mod_path / "aoc"
    if aoc_root.exists():
        model_roots.extend(path for path in aoc_root.glob("*/Model") if path.is_dir())

    candidates = []
    for model_root in model_roots:
        if not model_root.exists():
            continue
        candidates.extend(candidate for candidate in model_root.rglob(name) if candidate.is_file())

    def rank(path: Path):
        rel = path.relative_to(root_mod_path).parts
        group = 0 if rel[:2] == ("content", "Model") else 1
        return (group, len(rel), path.as_posix())

    return sorted(candidates, key=rank)


def _get_consumed_tex_dir(root_mod_path: Optional[Path]) -> Optional[Path]:
    if not root_mod_path:
        return None
    return root_mod_path / CONSUMED_TEX_DIR


def _get_consumed_tex_marker(file: Path, root_mod_path: Optional[Path]) -> Optional[Path]:
    if not root_mod_path:
        return None
    try:
        rel = file.relative_to(root_mod_path).as_posix()
    except ValueError:
        return None
    marker_dir = _get_consumed_tex_dir(root_mod_path)
    if marker_dir is None:
        return None
    return marker_dir / sha1(rel.encode("utf-8")).hexdigest()


def _mark_consumed_tex(file: Path, root_mod_path: Optional[Path]) -> None:
    marker = _get_consumed_tex_marker(file, root_mod_path)
    if marker is None:
        return
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("", encoding="utf-8")


def _is_consumed_tex(file: Path, root_mod_path: Optional[Path]) -> bool:
    marker = _get_consumed_tex_marker(file, root_mod_path)
    return bool(marker and marker.exists())


def _clear_consumed_tex_state(root_mod_path: Optional[Path]) -> None:
    state_dir = _get_consumed_tex_dir(root_mod_path)
    if state_dir and state_dir.exists():
        shutil.rmtree(state_dir, ignore_errors=True)

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
    source_file = _get_extracted_source_path(file) or file
    scope_root = _get_scope_root(source_file, root_mod_path)
    if not scope_root:
        return None

    candidates = _find_scoped_model_files(scope_root, tex1_name)
    if candidates:
        return candidates[0]

    if scope_root != root_mod_path:
        base_candidates = _find_base_model_files(root_mod_path, tex1_name)
        return base_candidates[0] if base_candidates else None

    return None


def _find_pack_owned_loose_texture_paths(file: Path, root_mod_path: Optional[Path]) -> list[Path]:
    source_file = _get_extracted_source_path(file) or file
    scope_root = _get_scope_root(source_file, root_mod_path)
    if not scope_root:
        return []

    consumed = []
    for name in (file.name.replace("Tex2", "Tex1"), file.name):
        consumed.extend(_find_scoped_model_files(scope_root, name))

    if not consumed and scope_root != root_mod_path:
        model_name = file.name.replace(".Tex2", "")
        if _find_base_model_files(root_mod_path, model_name):
            return []
        for name in (file.name.replace("Tex2", "Tex1"), file.name):
            consumed.extend(_find_base_model_files(root_mod_path, name))

    seen = set()
    unique = []
    for candidate in consumed:
        if candidate in seen:
            continue
        seen.add(candidate)
        unique.append(candidate)
    return unique


def _find_external_bfstm(file: Path, track_name: str, root_mod_path: Optional[Path]) -> Optional[Path]:
    search_root = root_mod_path or file.parent
    source_file = _get_extracted_source_path(file) or file
    scope_root = _get_scope_root(source_file, search_root)
    if not scope_root:
        return None

    candidates = _find_scoped_game_files(scope_root, track_name + ".bfstm")
    return candidates[0] if candidates else None


def _get_pack_owned_loose_texture_paths(file: Path, root_mod_path: Optional[Path]) -> list[Path]:
    if not root_mod_path or ".Tex2" not in file.suffixes:
        return []

    source_file = _get_extracted_source_path(file) or file
    scope_root = _get_scope_root(source_file, root_mod_path)
    if not scope_root:
        return []

    model_name = file.name.replace(".Tex2", "")
    if not (file.parent / model_name).exists():
        return []

    # If the model also exists loose in the same scope, do not suppress loose textures.
    if _find_scoped_model_files(scope_root, model_name):
        return []

    return _find_pack_owned_loose_texture_paths(file, root_mod_path)


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


def _format_rel_path(path: Path, root: Optional[Path]) -> str:
    if root:
        try:
            return str(path.relative_to(root))
        except ValueError:
            pass
    return str(path)


def _describe_tex1_source(tex1_path: Path, tex2_path: Path, root_mod_path: Optional[Path]) -> str:
    if not root_mod_path:
        return "external"

    source_file = _get_extracted_source_path(tex2_path) or tex2_path
    scope_root = _get_scope_root(source_file, root_mod_path)
    if scope_root:
        try:
            tex1_path.relative_to(scope_root)
            return "same scope"
        except ValueError:
            pass

    try:
        rel = tex1_path.relative_to(root_mod_path).parts
    except ValueError:
        return "external"

    if rel[:2] == ("content", "Model") or (len(rel) >= 3 and rel[0] == "aoc" and rel[2] == "Model"):
        return "base mod"
    return "external"


def _format_texture_path(file: Path, mod_path: Optional[Path], root_mod_path: Optional[Path]) -> str:
    if mod_path:
        try:
            return _format_conversion_target(file, mod_path)
        except ValueError:
            pass
    return _format_rel_path(file, root_mod_path)


def _log_texture_merge(tex1: str, tex2: str, output: str, source: Optional[str] = None) -> None:
    source_text = f"; source={source}" if source else ""
    with TEXTURE_LOG.open("a", encoding="utf-8") as log:
        log.write(f"Merged texture split: Tex1={tex1}; Tex2={tex2}{source_text}; output={output}\n")


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
            _log_texture_merge(
                _format_texture_path(sbfres, mod_path, root_mod_path),
                _format_texture_path(tex2, mod_path, root_mod_path),
                sbfres.with_name(f"{name.replace('Tex1', 'Tex')}{ext}").name,
            )
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
        for consumed in _get_pack_owned_loose_texture_paths(sbfres, root_mod_path):
            _mark_consumed_tex(consumed, root_mod_path)
        external_tex1 = _find_external_tex1(sbfres, root_mod_path)
        merged = None
        if external_tex1:
            merged = _merge_external_tex1_with_tex2(external_tex1, res_file, name)
            if merged:
                _log_texture_merge(
                    _format_texture_path(external_tex1, None, root_mod_path),
                    _format_texture_path(sbfres, mod_path, root_mod_path),
                    sbfres.with_name(f"{name}{ext}").name,
                    _describe_tex1_source(external_tex1, sbfres, root_mod_path),
                )
        else:
            stock_wiiu_tex1 = _get_stock_wiiu_tex1(sbfres)
            if stock_wiiu_tex1:
                merged = _merge_tex1_res_file_with_tex2(stock_wiiu_tex1, res_file, name)
                if merged:
                    _log_texture_merge(
                        f"stock Wii U {sbfres.name.replace('Tex2', 'Tex1')}",
                        _format_texture_path(sbfres, mod_path, root_mod_path),
                        sbfres.with_name(f"{name}{ext}").name,
                    )
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
        result = run(
            [str(readwrite), str(work_hkx)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        if result.returncode != 0:
            if stdout:
                logger.error(
                    "ReadWrite.exe stdout for %s:\n%s",
                    hkx.as_posix(),
                    stdout,
                )
            if stderr:
                logger.error(
                    "ReadWrite.exe stderr for %s:\n%s",
                    hkx.as_posix(),
                    stderr,
                )
            raise RuntimeError(
                f"ReadWrite.exe failed for {hkx.name} with exit code {result.returncode}"
            )
        if not out_hkx.exists():
            if stdout:
                logger.error(
                    "ReadWrite.exe stdout for %s:\n%s",
                    hkx.as_posix(),
                    stdout,
                )
            if stderr:
                logger.error(
                    "ReadWrite.exe stderr for %s:\n%s",
                    hkx.as_posix(),
                    stderr,
                )
            raise FileNotFoundError(f"ReadWrite.exe did not produce output file for {hkx.name}")
        out_bytes = out_hkx.read_bytes()
        hkx.write_bytes(oead.yaz0.compress(out_bytes) if compress_back else out_bytes)
    finally:
        if out_hkx.exists():
            out_hkx.unlink()
        if work_hkx != hkx and work_hkx.exists():
            work_hkx.unlink()

def convert_sbeco(sbeco: Path) -> None:
    raw_bytes = sbeco.read_bytes()
    compressed = raw_bytes[:4] == b"Yaz0"
    beco_bytes = util.unyaz_if_needed(raw_bytes)
    converted = convert_sbeco_bytes(beco_bytes)
    sbeco.write_bytes(oead.yaz0.compress(converted) if compressed else converted)


def convert_byml_file(byml_file: Path) -> None:
    raw_bytes = byml_file.read_bytes()
    compressed = raw_bytes[:4] == b"Yaz0"
    byml = oead.byml.from_binary(util.unyaz_if_needed(raw_bytes))
    converted = oead.byml.to_binary(byml, big_endian=False)
    byml_file.write_bytes(util.compress(converted) if compressed else converted)


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

    if any("bflim" in i.name for i in blarc.get_files()):
        # Get the pack file where the sblarc comes from
        stock_pack = util.get_game_file(f"Pack/{pack_name}")
        stock_sblarc = oead.Sarc(util.unyaz_if_needed(stock_pack.read_bytes())).get_file(f"Layout/{sblarc.name}")
        if not isinstance(stock_sblarc, oead.File):
            raise FileNotFoundError(f"Layout/{sblarc.name} was not found in stock {pack_name}.")
        stock_blarc = oead.Sarc(util.unyaz_if_needed(stock_sblarc.data))
        bntx_file = stock_blarc.get_file("timg/__Combined.bntx")
        extract_sarc(blarc, blarc_path, sblarc)
        Path(blarc_path / bntx_file.name).write_bytes(bntx_file.data)

        for bflim in blarc_path.rglob('*.bflim'):
            try:
                # Inject every bflim found into the bntx file
                injected = bntx.tex_inject(blarc_path / bntx_file.name, bflim)
                if not injected:
                    logging.warning(f"{bflim.relative_to(blarc_path)} could not be converted")
                    continue
                Path(bflim).unlink()
            except Exception as err:
                logging.warning(f"{bflim.relative_to(blarc_path)} could not be converted")
                logging.debug(err, exc_info=True)
        # Write the new blarc file
        write_sarc(blarc, blarc_path, sblarc)

        # Remove the temporary folder
        _cleanup_temp_extract_path(blarc_path)

def convert_bflyt_sblarc(sblarc: Path) -> None:
    # Convert bflyt/bflan files inside a WiiU sblarc
    blarc = oead.Sarc(util.unyaz_if_needed(sblarc.read_bytes()))
    blarc_path = _get_temp_extract_path(sblarc)

    if any(i.name.endswith((".bflyt", ".bflan")) for i in blarc.get_files()):
        extract_sarc(blarc, blarc_path, sblarc)
        try:
            for bflyt in blarc_path.rglob('*.bflyt'):
                try:
                    convert_bflyt_layoutu(bflyt)
                except Exception as err:
                    logging.warning(f"{bflyt.relative_to(blarc_path)} could not be converted")
                    logging.debug(err, exc_info=True)

            for bflan in blarc_path.rglob('*.bflan'):
                try:
                    convert_bflan_layoutu(bflan)
                except Exception as err:
                    logging.warning(f"{bflan.relative_to(blarc_path)} could not be converted")
                    logging.debug(err, exc_info=True)

            write_sarc(blarc, blarc_path, sblarc)
        finally:
            _cleanup_temp_extract_path(blarc_path)

def warn_unhandled_sblarc_files(sblarc: Path) -> None:
    blarc = oead.Sarc(util.unyaz_if_needed(sblarc.read_bytes()))
    for file in blarc.get_files():
        suffix = Path(file.name).suffix
        if suffix in {".bflim", ".bflyt", ".bflan", ".bntx"}:
            continue

        data = bytes(file.data)
        content_format = _detect_content_format(data)
        if content_format == "aamp" or content_format in COMPATIBLE_EXT:
            continue

        content_label = content_format or f"magic {_get_inner_magic(data).hex()} / extension {suffix or '<none>'}"
        logging.warning(
            f"{sblarc.name} -> {file.name} could not be converted: no converter for {content_label}; file was left unchanged"
        )

def change_platform(file: Path, mod_path: Path, root_mod_path: Path = None) -> None:
    content_format = _detect_content_format(file.read_bytes())

    if file.suffix in BFRES_EXT or content_format == "bfres":
        # Convert FRES files
        if ".Tex2" not in file.suffixes or not Path(str(file).replace("Tex2", "Tex1")).exists():
            convert_bfres(file, mod_path, root_mod_path)

    elif file.suffix == ".bars" or content_format == "bars":
        # Convert bars files
        bars_bytes = bytearray(file.read_bytes())
        tracks, offsets = bars.get_bars_tracks(bars_bytes)
        for name, data in tracks.items():
            # Read the track header and convert appropiately
            magic: str = data[:0x4].decode("utf-8")
            bfstm_exists = _find_external_bfstm(file, name, root_mod_path or mod_path)

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

    elif file.suffix == ".bfstm" or content_format == "bfstm":
        # Convert BFSTM files
        new_bfstm = bcf_converter.conv_file(file.read_bytes(), "FSTM", '<')
        file.write_bytes(bytes(new_bfstm))
        print("Successfully converted " + file.name + "!")

    elif file.suffix == ".sbeco" or content_format == "beco":
        # Convert BECO coordinate maps from Wii U big endian to Switch little endian.
        convert_sbeco(file)
        print("Successfully converted " + file.name + "!")

    elif file.suffix == ".bflyt" or content_format == "bflyt":
        # Convert layout files
        convert_bflyt_layoutu(file)
        print("Successfully converted " + file.name + "!")

    elif file.suffix == ".bflan" or content_format == "bflan":
        convert_bflan_layoutu(file)
        print("Successfully converted " + file.name + "!")

    elif content_format == "byml":
        convert_byml_file(file)
        print("Successfully converted " + file.name + "!")

    elif (
        ("pack" in file.suffix and file.suffix != ".sbquestpack")
        or (file.suffix != ".sblarc" and content_format == "sarc")
    ):
        # Convert files inside of pack files
        pack = oead.Sarc(util.unyaz_if_needed(file.read_bytes()))
        pack_path = _get_temp_extract_path(file)
        try:
            extract_sarc(pack, pack_path, file)
            new_files = pack_path.rglob('*.*')
            for new in new_files:
                try:
                    convert_files(new, pack_path, root_mod_path or mod_path)
                except Exception as err:
                    logger.warning(f"{_format_conversion_target(new, pack_path)} could not be converted")
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
            # Convert bflyt files inside of sblarc files
            convert_bflyt_sblarc(file)
            warn_unhandled_sblarc_files(file)

    elif file.suffix in HAVOK_EXT or content_format == "havok":
        # Convert havok files
        convert_havok(file)

    elif file.suffix not in COMPATIBLE_EXT:
        if content_format in ("aamp", "xml") or content_format in COMPATIBLE_EXT:
            pass
        else:
            magic = _get_inner_magic(file.read_bytes()).hex()
            raise ValueError(
                f"No conversion handler for file {file.name} with suffix {file.suffix} and magic {magic}"
            )

def convert_files(file: Path, mod_path: Path, root_mod_path = None) -> None:
    if not file.exists() or file.stat().st_size == 0:
        return

    if (
        root_mod_path
        and file.suffix in BFRES_EXT
        and any(tag in file.suffixes for tag in (".Tex1", ".Tex2"))
        and _is_consumed_tex(file, root_mod_path)
    ):
        file.unlink(missing_ok=True)
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

    if file.suffix == ".sbeco":
        try:
            change_platform(file, mod_path, root_mod_path)
        except Exception as err:
            logger.warning(f"{_format_conversion_target(file, mod_path)} could not be converted")
            logger.debug(err, exc_info=True)
        return

    file_data = file.read_bytes()

    try:
        content_format = _detect_content_format(file_data)
        if content_format in ("aamp", "xml") or content_format in COMPATIBLE_EXT:
            return
        if file.suffix not in COMPATIBLE_EXT and content_format is not None:
            change_platform(file, mod_path, root_mod_path)
            return
    except Exception as err:
        logger.warning(f"{_format_conversion_target(file, mod_path)} could not be converted")
        logger.debug(err, exc_info=True)
        return

    try:
        canon = util.get_canon_name(file.relative_to(mod_path), allow_no_source=True)
        is_modded = is_file_modded(canon, file_data)

        # Sesetlist: always convert via EMTR patch regardless of is_modded
        if file.suffix == ".sesetlist":
            rel = _get_game_rel(file, mod_path)
            if mod_path.parent != SCRIPT and rel is not None:
                # Loose sesetlist
                stock_sw = util.get_game_file(rel)
                stock_wu = _get_wiiu_game_file(rel)
                converted = bytes(convert_sesetlist(file_data, stock_wu.read_bytes(), stock_sw.read_bytes()))
                file.write_bytes(converted)
            else:
                # Inside extracted SARC (called from change_platform via extract_sarc)
                source_path_file = mod_path / SOURCE_PATH_FILE
                if source_path_file.exists():
                    original_pack = Path(source_path_file.read_text(encoding="utf-8"))
                    root = root_mod_path or mod_path
                    sesetlist_name = file.relative_to(mod_path).as_posix()
                    try:
                        stock_wu, stock_sw = _get_stock_sesetlist_pair(root, original_pack, sesetlist_name)
                        converted = bytes(convert_sesetlist(file_data, stock_wu, stock_sw))
                        file.write_bytes(converted)
                        _set_wrapper_state(mod_path, sesetlist_name, converted[:4] == b"Yaz0")
                    except Exception as err:
                        logger.warning(
                            f"{_format_conversion_target(file, mod_path)} could not be converted: {err}"
                        )
                        logger.debug("sesetlist in pack %s: %s", sesetlist_name, err)
            return

        handled = False

        # Convert supported files
        if is_modded or file.suffix == ".bars": 
            change_platform(file, mod_path, root_mod_path)
            handled = True
            
        elif file.suffix in NO_CONVERT_EXTS:
            logger.warning(
                f"{_format_conversion_target(file, mod_path)} is unsupported by the pre-converter and was left unchanged"
            )
            handled = True

        if not handled and file.suffix not in COMPATIBLE_EXT:
            content_label = content_format or f"magic {_get_inner_magic(file_data).hex()} / extension {file.suffix or '<none>'}"
            logger.warning(
                f"{_format_conversion_target(file, mod_path)} could not be converted: no converter for {content_label}; file was left unchanged"
            )
                
    except Exception as err:
        logger.warning(f"{_format_conversion_target(file, mod_path)} could not be converted")
        logger.debug(err, exc_info=True)

def convert(mod: Path) -> None:
    # Open the mod
    mod_path = open_mod(mod)
    temp_extract_root = _get_temp_extract_root(mod_path / "info.json")
    try:
        _clear_consumed_tex_state(mod_path)
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
        _clear_consumed_tex_state(mod_path)
        _remove_dummy_byml_placeholders(mod_path)
        warnings = convert_mod(mod_path, False, True)

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
                        # if warning.startswith("This mod contains a file not supported by the converter: "):
                        #     warning = warning.replace(
                        #         "This mod contains a file not supported by the converter: ",
                        #         "BCML marked this file as unsupported via NO_CONVERT_EXTS: ",
                        #         1,
                        #     )
                        logger.warning(warning)

    except Exception as err:
        print(traceback.format_exc())

    finally:
        # Remove the temporary mod_path
        _clear_consumed_tex_state(mod_path)
        shutil.rmtree(mod_path, ignore_errors=True)
        shutil.rmtree(temp_extract_root, ignore_errors=True)

def main() -> None:
    ERROR_LOG.write_text("", encoding="utf-8")
    TEXTURE_LOG.write_text("", encoding="utf-8")

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
