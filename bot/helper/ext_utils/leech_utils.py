from hashlib import md5
from time import strftime, gmtime, time
from re import IGNORECASE, sub as re_sub, search as re_search, findall as re_findall, compile as re_compile
from shlex import split as ssplit
from natsort import natsorted
from os import path as ospath
from aiofiles.os import remove as aioremove, path as aiopath, mkdir, makedirs, listdir
from aioshutil import rmtree as aiormtree
from contextlib import suppress
from json import loads as json_loads
from asyncio import create_subprocess_exec, create_task, gather, Semaphore
from asyncio.subprocess import PIPE
from telegraph import upload_file
from langcodes import Language

from bot import bot_cache, LOGGER, MAX_SPLIT_SIZE, config_dict, user_data
from bot.modules.mediainfo import parseinfo
from bot.helper.ext_utils.bot_utils import cmd_exec, sync_to_async, get_readable_file_size, get_readable_time
from bot.helper.ext_utils.fs_utils import ARCH_EXT, get_mime_type
from bot.helper.ext_utils.telegraph_helper import telegraph


async def is_multi_streams(path):
    try:
        result = await cmd_exec(["ffprobe", "-hide_banner", "-loglevel", "error", "-print_format",
                                 "json", "-show_streams", path])
        if res := result[1]:
            LOGGER.warning(f'Get Video Streams: {res}')
    except Exception as e:
        LOGGER.error(f'Get Video Streams: {e}. Mostly File not found!')
        return False
    fields = json_loads(result[0]).get('streams')
    if fields is None:
        LOGGER.error(f"get_video_streams: {result}")
        return False
    videos = 0
    audios = 0
    for stream in fields:
        if stream.get('codec_type') == 'video':
            videos += 1
        elif stream.get('codec_type') == 'audio':
            audios += 1
    return videos > 1 or audios > 1


async def get_media_info(path, metadata=False):
    try:
        result = await cmd_exec(["ffprobe", "-hide_banner", "-loglevel", "error", "-print_format",
                                 "json", "-show_format", "-show_streams", path])
        if res := result[1]:
            LOGGER.warning(f'Media Info FF: {res}')
    except Exception as e:
        LOGGER.error(f'Media Info: {e}. Mostly File not found!')
        return (0, "", "", "") if metadata else (0, None, None)
    ffresult = json_loads(result[0])
    fields = ffresult.get('format')
    if fields is None:
        LOGGER.error(f"Media Info Sections: {result}")
        return (0, "", "", "") if metadata else (0, None, None)
    duration = round(float(fields.get('duration', 0)))
    if metadata:
        lang, qual, stitles = "", "", ""
        if (streams := ffresult.get('streams')) and streams[0].get('codec_type') == 'video':
            qual = int(streams[0].get('height'))
            qual = f"{480 if qual <= 480 else 540 if qual <= 540 else 720 if qual <= 720 else 1080 if qual <= 1080 else 2160 if qual <= 2160 else 4320 if qual <= 4320 else 8640}p"
            for stream in streams:
                if stream.get('codec_type') == 'audio' and (lc := stream.get('tags', {}).get('language')):
                    with suppress(Exception):
                        lc = Language.get(lc).display_name()
                    if lc not in lang:
                        lang += f"{lc}, "
                if stream.get('codec_type') == 'subtitle' and (st := stream.get('tags', {}).get('language')):
                    with suppress(Exception):
                        st = Language.get(st).display_name()
                    if st not in stitles:
                        stitles += f"{st}, "
        return duration, qual, lang[:-2], stitles[:-2]
    tags = fields.get('tags', {})
    artist = tags.get('artist') or tags.get('ARTIST') or tags.get("Artist")
    title = tags.get('title') or tags.get('TITLE') or tags.get("Title")
    return duration, artist, title


async def get_document_type(path):
    is_video, is_audio, is_image = False, False, False
    if path.endswith(tuple(ARCH_EXT)) or re_search(r'.+(\.|_)(rar|7z|zip|bin)(\.0*\d+)?$', path):
        return is_video, is_audio, is_image
    mime_type = await sync_to_async(get_mime_type, path)
    if mime_type.startswith('audio'):
        return False, True, False
    if mime_type.startswith('image'):
        return False, False, True
    if not mime_type.startswith('video') and not mime_type.endswith('octet-stream'):
        return is_video, is_audio, is_image
    try:
        result = await cmd_exec(["ffprobe", "-hide_banner", "-loglevel", "error", "-print_format",
                                 "json", "-show_streams", path])
        if res := result[1]:
            LOGGER.warning(f'Get Document Type: {res}')
    except Exception as e:
        LOGGER.error(f'Get Document Type: {e}. Mostly File not found!')
        return is_video, is_audio, is_image
    fields = json_loads(result[0]).get('streams')
    if fields is None:
        LOGGER.error(f"get_document_type: {result}")
        return is_video, is_audio, is_image
    for stream in fields:
        if stream.get('codec_type') == 'video':
            is_video = True
        elif stream.get('codec_type') == 'audio':
            is_audio = True
    return is_video, is_audio, is_image


async def get_audio_thumb(audio_file):
    des_dir = 'Thumbnails'
    if not await aiopath.exists(des_dir):
        await mkdir(des_dir)
    des_dir = ospath.join(des_dir, f"{time()}.jpg")
    cmd = [bot_cache['pkgs'][2], "-hide_banner", "-loglevel", "error",
           "-i", audio_file, "-an", "-vcodec", "copy", des_dir]
    status = await create_subprocess_exec(*cmd, stderr=PIPE)
    if await status.wait() != 0 or not await aiopath.exists(des_dir):
        err = (await status.stderr.read()).decode().strip()
        LOGGER.error(
            f'Error while extracting thumbnail from audio. Name: {audio_file} stderr: {err}')
        return None
    return des_dir


async def take_ss(video_file, duration=None, total=1, gen_ss=False):
    des_dir = ospath.join('Thumbnails', f"{time()}")
    await makedirs(des_dir, exist_ok=True)
    if duration is None:
        duration = (await get_media_info(video_file))[0]
    if duration == 0:
        duration = 3
    duration = duration - (duration * 2 / 100)
    cmd = [bot_cache['pkgs'][2], "-hide_banner", "-loglevel", "error", "-ss", "",
           "-i", video_file, "-vf", "thumbnail", "-frames:v", "1", des_dir]
    tstamps = {}
    thumb_sem = Semaphore(3)

    async def extract_ss(eq_thumb):
        async with thumb_sem:
            cmd[5] = str((duration // total) * eq_thumb)
            tstamps[f"wz_thumb_{eq_thumb}.jpg"] = strftime("%H:%M:%S", gmtime(float(cmd[5])))
            cmd[-1] = ospath.join(des_dir, f"wz_thumb_{eq_thumb}.jpg")
            task = await create_subprocess_exec(*cmd, stderr=PIPE)
            return (task, await task.wait(), eq_thumb)

    tasks = [extract_ss(eq_thumb) for eq_thumb in range(1, total+1)]
    status = await gather(*tasks)

    for task, rtype, eq_thumb in status:
        if rtype != 0 or not await aiopath.exists(ospath.join(des_dir, f"wz_thumb_{eq_thumb}.jpg")):
            err = (await task.stderr.read()).decode().strip()
            LOGGER.error(f'Error while extracting thumbnail no. {eq_thumb} from video. Name: {video_file} stderr: {err}')
            await aiormtree(des_dir)
            return None
    return (des_dir, tstamps) if gen_ss else ospath.join(des_dir, "wz_thumb_1.jpg")


async def split_file(path, size, file_, dirpath, split_size, listener, start_time=0, i=1, inLoop=False, multi_streams=True):
    if listener.suproc == 'cancelled' or listener.suproc is not None and listener.suproc.returncode == -9:
        return False
    if listener.seed and not listener.newDir:
        dirpath = f"{dirpath}/splited_files_mltb"
        if not await aiopath.exists(dirpath):
            await mkdir(dirpath)
    user_id = listener.message.from_user.id
    user_dict = user_data.get(user_id, {})
    leech_split_size = user_dict.get(
        'split_size') or config_dict['LEECH_SPLIT_SIZE']
    parts = -(-size // leech_split_size)
    if (user_dict.get('equal_splits') or config_dict['EQUAL_SPLITS'] and 'equal_splits' not in user_dict) and not inLoop:
        split_size = ((size + parts - 1) // parts) + 1000
    if (await get_document_type(path))[0]:
        if multi_streams:
            multi_streams = await is_multi_streams(path)
        duration = (await get_media_info(path))[0]
        base_name, extension = ospath.splitext(file_)
        split_size -= 5000000
        while i <= parts or start_time < duration - 4:
            parted_name = f"{base_name}.part{i:03}{extension}"
            out_path = ospath.join(dirpath, parted_name)
            cmd = [bot_cache['pkgs'][2], "-hide_banner", "-loglevel", "error", "-ss", str(start_time), "-i", path,
                   "-fs", str(split_size), "-map", "0", "-map_chapters", "-1", "-async", "1", "-strict",
                   "-2", "-c", "copy", out_path]
            if not multi_streams:
                del cmd[10]
                del cmd[10]
            if listener.suproc == 'cancelled' or listener.suproc is not None and listener.suproc.returncode == -9:
                return False
            listener.suproc = await create_subprocess_exec(*cmd, stderr=PIPE)
            code = await listener.suproc.wait()
            if code == -9:
                return False
            elif code != 0:
                err = (await listener.suproc.stderr.read()).decode().strip()
                try:
                    await aioremove(out_path)
                except Exception:
                    pass
                if multi_streams:
                    LOGGER.warning(
                        f"{err}. Retrying without map, -map 0 not working in all situations. Path: {path}")
                    return await split_file(path, size, file_, dirpath, split_size, listener, start_time, i, True, False)
                else:
                    LOGGER.warning(
                        f"{err}. Unable to split this video, if it's size less than {MAX_SPLIT_SIZE} will be uploaded as it is. Path: {path}")
                return "errored"
            out_size = await aiopath.getsize(out_path)
            if out_size > MAX_SPLIT_SIZE:
                dif = out_size - MAX_SPLIT_SIZE
                split_size -= dif + 5000000
                await aioremove(out_path)
                return await split_file(path, size, file_, dirpath, split_size, listener, start_time, i, True, )
            lpd = (await get_media_info(out_path))[0]
            if lpd == 0:
                LOGGER.error(
                    f'Something went wrong while splitting, mostly file is corrupted. Path: {path}')
                break
            elif duration == lpd:
                LOGGER.warning(
                    f"This file has been splitted with default stream and audio, so you will only see one part with less size from orginal one because it doesn't have all streams and audios. This happens mostly with MKV videos. Path: {path}")
                break
            elif lpd <= 3:
                await aioremove(out_path)
                break
            start_time += lpd - 3
            i += 1
    else:
        out_path = ospath.join(dirpath, f"{file_}.")
        listener.suproc = await create_subprocess_exec("split", "--numeric-suffixes=1", "--suffix-length=3",
                                                       f"--bytes={split_size}", path, out_path, stderr=PIPE)
        code = await listener.suproc.wait()
        if code == -9:
            return False
        elif code != 0:
            err = (await listener.suproc.stderr.read()).decode().strip()
            LOGGER.error(err)
    return True


async def get_ss(up_path, ss_no):
    thumbs_path, tstamps = await take_ss(up_path, total=min(ss_no, 250), gen_ss=True)
    th_html = f"📌 <h4>{ospath.basename(up_path)}</h4><br>📇 <b>Total Screenshots:</b> {ss_no}<br><br>"
    up_sem = Semaphore(25)
    async def telefile(thumb):
        async with up_sem:
            tele_id = await sync_to_async(upload_file, ospath.join(thumbs_path, thumb))
            return tele_id[0], tstamps[thumb]
    tasks = [telefile(thumb) for thumb in natsorted(await listdir(thumbs_path))]
    results = await gather(*tasks)
    th_html += ''.join(f'<img src="https://graph.org{tele_id}"><br><pre>Screenshot at {stamp}</pre>' for tele_id, stamp in results)
    await aiormtree(thumbs_path)
    link_id = (await telegraph.create_page(title="ScreenShots X", content=th_html))["path"]
    return f"https://graph.org/{link_id}"


async def get_mediainfo_link(up_path):
    stdout, __, _ = await cmd_exec(ssplit(f'mediainfo "{up_path}"'))
    tc = f"📌 <h4>{ospath.basename(up_path)}</h4><br><br>"
    if len(stdout) != 0:
        tc += parseinfo(stdout)
    link_id = (await telegraph.create_page(title="MediaInfo X", content=tc))["path"]
    return f"https://graph.org/{link_id}"


def get_md5_hash(up_path):
    md5_hash = md5()
    with open(up_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            md5_hash.update(byte_block)
        return md5_hash.hexdigest()


# Patterns
# Pattern 1: S01E02 or S01EP02 or S01_E01
PATTERN1 = re_compile(r'S(\d+)[._\s-]*(?:E|EP)(\d+)', IGNORECASE)
# Pattern 2: S01 E02 or S01 EP02 or S01 - E01 or S01 - EP02 or S01_01
PATTERN2 = re_compile(r'S(\d+)[._\s-]*(\d+)', IGNORECASE)
# Pattern 3: Episode Number After "E" or "EP"
PATTERN3 = re_compile(r'(?:[([<{]?\s*(?:E|EP)[._\s-]*(\d+)\s*[)\]>}]?)', IGNORECASE)
# Pattern 3_2: episode number after - [hyphen]
PATTERN3_2 = re_compile(r'(?:\s*-\s*(\d+)\s*)')
# Pattern 4: S2 09 ex.
PATTERN4 = re_compile(r'S(\d+)[^\d]*(\d+)', IGNORECASE)
# Pattern X: Standalone Episode Number
PATTERNX = re_compile(r'(\d+)')
# QUALITY PATTERNS
# Pattern 5: 3-4 digits followed by 'p' as quality
PATTERN5 = re_compile(r'(\d{3,4}p)', IGNORECASE)
# Pattern 6: Find 4k in brackets or parentheses
PATTERN6 = re_compile(r'[([<{]?\s*4k\s*[)\]>}]?', IGNORECASE)
# Pattern 7: Find 2k in brackets or parentheses
PATTERN7 = re_compile(r'[([<{]?\s*2k\s*[)\]>}]?', IGNORECASE)
# Pattern 8: Find HdRip without spaces
PATTERN8 = re_compile(r'[([<{]?\s*HdRip\s*[)\]>}]?|\bHdRip\b', IGNORECASE)
# Pattern 9: Find 4kX264 in brackets or parentheses
PATTERN9 = re_compile(r'[([<{]?\s*4kX264\s*[)\]>}]?', IGNORECASE)
# Pattern 10: Find 4kx265 in brackets or parentheses
PATTERN10 = re_compile(r'[([<{]?\s*4kx265\s*[)\]>}]?', IGNORECASE)

# SEASON PATTERNS
# Pattern 11: S01 or S 01 or Season 01
PATTERN11 = re_compile(r'S(?:eason)?[._\s-]*(\d+)', IGNORECASE)

# AUDIO PATTERNS
# Pattern 12: Dual Audio, Multi Audio, Hindi, English etc.
PATTERN12 = re_compile(r'[([<{]?\s*(Dual Audio|Multi Audio|Hindi|English|Tamil|Telugu|Malayalam|Kannada|Bengali|Gujarati|Punjabi|Marathi|Dual-Audio)\s*[)\]>}]?', IGNORECASE)

# MANGA PATTERNS
# Pattern 13: Volume Number (V01, Vol 01, Volume 01)
PATTERN13 = re_compile(r'V(?:ol(?:ume)?)?[._\s-]*(\d+)', IGNORECASE)
# Pattern 14: Chapter Number (C01, Ch 01, Chapter 01)
PATTERN14 = re_compile(r'C(?:h(?:apter)?)?[._\s-]*(\d+)', IGNORECASE)


def sanitize_filename(filename):
    # Remove invalid characters and trailing dots/spaces
    filename = re_sub(r'[\/*?:"<>|]', '', filename)
    return filename.strip().strip('.')

def clean_filename(filename):
    # Remove the telegram prefix if it exists (e.g. 8584729307_1773321117_BQACAgQA_)
    filename = re_sub(r'^\d+_\d+_[A-Za-z0-9]+_', '', filename)
    # Replace underscores with spaces
    filename = filename.replace('_', ' ')
    # Remove extra spaces
    filename = ' '.join(filename.split())
    return filename

def extract_season(filename):
    match = re_search(PATTERN11, filename)
    if match:
        return match.group(1)
    return ""

def extract_episode(filename):
    for pattern in [PATTERN1, PATTERN2, PATTERN3, PATTERN3_2, PATTERN4, PATTERNX]:
        match = re_search(pattern, filename)
        if match:
            if pattern in [PATTERN1, PATTERN2, PATTERN4]:
                return match.group(2)
            return match.group(1)
    return ""

def extract_audio(filename):
    match = re_search(PATTERN12, filename)
    if match:
        return match.group(1)
    return ""

def extract_volume(filename):
    match = re_search(PATTERN13, filename)
    if match:
        return match.group(1)
    return ""

def extract_chapter(filename):
    match = re_search(PATTERN14, filename)
    if match:
        return match.group(1)
    return ""

def extract_quality(filename):
    # Try Quality Patterns
    if match := re_search(PATTERN5, filename):
        return match.group(1)
    if re_search(PATTERN6, filename):
        return "4k"
    if re_search(PATTERN7, filename):
        return "2k"
    if re_search(PATTERN8, filename):
        return "HdRip"
    if re_search(PATTERN9, filename):
        return "4kX264"
    if re_search(PATTERN10, filename):
        return "4kx265"
    return ""


async def auto_rename(filename, user_id):
    if not filename:
        return filename
    name, ext = ospath.splitext(filename)
    cleaned_name = clean_filename(name)

    season = extract_season(cleaned_name)
    episode = extract_episode(cleaned_name)
    audio = extract_audio(cleaned_name).replace("-", " ")
    quality = extract_quality(cleaned_name)
    volume = extract_volume(cleaned_name)
    chapter = extract_chapter(cleaned_name)

    user_dict = user_data.get(user_id, {})
    template = user_dict.get('lauto_rename') or "{filename}.{ext}"

    new_name = template
    new_name = new_name.replace('{filename}', cleaned_name)
    new_name = new_name.replace('{season}', season)
    new_name = new_name.replace('{episode}', episode)
    new_name = new_name.replace('{audio}', audio)
    new_name = new_name.replace('{quality}', quality)
    new_name = new_name.replace('{volume}', volume)
    new_name = new_name.replace('{chapter}', chapter)
    new_name = new_name.replace('{ext}', ext.lstrip('.'))

    # Remove any empty placeholders or double spaces resulting from empty extractions
    new_name = re_sub(r'\{\w+\}', '', new_name)
    new_name = ' '.join(new_name.split())

    return sanitize_filename(new_name)
