"""Pausing whatever is playing while a dictation records, and putting it back.

Dictating over music means the microphone hears the music, and Whisper
transcribes what it hears - including a line of a song in the middle of a
sentence. The fix is to pause the player for the length of the recording.

This goes through the System Media Transport Controls, the same interface the
media keys on a keyboard use, so it reaches anything that registers with
Windows: YouTube in a browser, Spotify, Groove, VLC. Two rules make it safe to
do automatically:

* only what is **playing right now** is paused, and
* only those exact players are resumed, and only if they are still paused.

So a dictation never starts playing something the user had stopped, and never
leaves a player paused that it did not pause. A player with no stable app id is
left alone, because it could not be resumed reliably afterwards.

The alternative - sending the play/pause media key - was rejected: it is blind.
It cannot tell whether anything was playing, so when nothing was it starts the
music instead of pausing it.

`winsdk` is optional. Without it this module reports itself unavailable and
every call does nothing, which is the correct behaviour for a feature that is
a convenience rather than part of the recording.
"""
from __future__ import annotations

try:
    from winsdk.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as _Manager,
    )
    from winsdk.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as _Status,
    )
    _HAVE_WINSDK = True
except Exception:
    _HAVE_WINSDK = False


def available() -> bool:
    """Whether pausing can be attempted at all on this installation."""
    return _HAVE_WINSDK


async def _pause_async() -> list[str]:
    manager = await _Manager.request_async()
    paused: list[str] = []
    for session in manager.get_sessions():
        try:
            app_id = session.source_app_user_model_id
            if not app_id:
                continue            # nothing to resume it by later; leave it
            info = session.get_playback_info()
            if info and info.playback_status == _Status.PLAYING:
                if await session.try_pause_async():
                    paused.append(app_id)
        except Exception:
            pass                    # one uncooperative player, not a failure
    return paused


async def _resume_async(app_ids: list[str]) -> None:
    wanted = set(app_ids)
    manager = await _Manager.request_async()
    for session in manager.get_sessions():
        try:
            if session.source_app_user_model_id not in wanted:
                continue
            info = session.get_playback_info()
            # Still paused: resume it. Stopped or closed in the meantime: leave
            # it, so this never *starts* playback the user did not want.
            if info and info.playback_status == _Status.PAUSED:
                await session.try_play_async()
        except Exception:
            pass


def pause_playing() -> list[str]:
    """Pause everything currently playing. Returns the ids to pass to resume().

    Never raises, and returns an empty list when there is nothing playing or
    the interface is unavailable - so the caller can treat "nothing happened"
    and "it worked" the same way.
    """
    if not _HAVE_WINSDK:
        return []
    try:
        import asyncio

        return asyncio.run(_pause_async())
    except Exception:
        return []


def resume(app_ids: list[str]) -> None:
    """Put back exactly what pause_playing() paused. Never raises."""
    if not _HAVE_WINSDK or not app_ids:
        return
    try:
        import asyncio

        asyncio.run(_resume_async(list(app_ids)))
    except Exception:
        pass
