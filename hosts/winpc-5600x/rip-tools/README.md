# rip-tools (winpc-5600x / daedalus)

Configuration of the three ripping tools on daedalus, captured read-only on
2026-11-03 so it is under version control. The files here are a **snapshot**,
not a deployment: nothing applies them automatically, and the live config on
the box can drift from them. Re-capture after changing a setting on the box.

⚠️ **This repo is public.** Secrets were filtered out on daedalus before the
values were read. Any value whose name matched
`key|token|pass|secret|oauth|email|user|login|auth|cookie|session` (case-insensitive)
was replaced, along with a few more listed per tool below. Redacted entries are
commented out with `;` and their values are not in this repo.

| Tool | Version | Live config on daedalus | Captured as |
| :--- | :--- | :--- | :--- |
| Exact Audio Copy | 1.8 | registry `HKCU\Software\AWSoftware\EACU` | [`eac/EAC.reg`](eac/EAC.reg) |
| FLAC (bundled with EAC) | 1.4.3 | `C:\Program Files (x86)\Exact Audio Copy\Flac\FLAC.EXE` | (no config of its own) |
| MakeMKV | v1.18.4 | registry `HKCU\Software\MakeMKV` | [`makemkv/MakeMKV.reg`](makemkv/MakeMKV.reg) |
| MusicBrainz Picard | 3.0.2 | `%APPDATA%\MusicBrainz\Picard.ini` | [`picard/Picard.ini`](picard/Picard.ini) |
| ffmpeg (transcode pipeline) | `N-125716-g1b1f602699-20260722` (master win64 GPL build) | `C:\ffmpeg\ffmpeg-master-latest-win64-gpl\bin` (on `PATH`) | (no config) |

Versions come from the Windows uninstall entries (EAC's `EAC.exe` carries no
file version), the exe file versions, `flac --version` and `ffmpeg -version`.

## Exact Audio Copy

EAC keeps its settings in the registry, not in a file. `%APPDATA%\EAC\` holds
only `CDDB.sdf` (the local metadata cache) and an **empty** `Profiles` folder.
`UseProfile` is empty, so no named profile is in use. The registry is the whole
configuration.

Settings that matter, by registry value:

| Setting | Value | Why it matters |
| :--- | :--- | :--- |
| Output dir (`Extraction Options\DirectorySpecification`, `StartUp Options\ActualPath`) | `C:\Rips\` | Where `import-music.ps1` looks |
| File naming (`FileNamingConvention`, and the 2nd/various variants) | `%tracknr2% %title%` | Gives the flat `NN Title.flac` that `import-music.ps1` expects |
| Character replacement (`ReplacementRule0`–`8`) | `"`→`''`, `\`→`-`, `/`→`,`, `:`→`-`, `*`→`x`, `?`→removed, `<`→`[`, `>`→`]`, `\|`→`!` | Windows-illegal characters in titles |
| Encoder (`Compression Options\ExternalEncoderProgram`) | the bundled `FLAC.EXE` | flac 1.4.3 |
| Encoder options (`ExternalEncoderOptions`) | `-6 -V` plus `-T` tags for artist, title, album, date, track, genre, comment, album artist, composer, disc number and totals, plus lyrics and `--picture` cover when present | Level 6 with verify. Tags are written at rip time, and Picard then corrects them |
| `ExternalEncoderDeleteSource` / `ExternalEncoderCheckReturnCode` | on / on | The WAV is removed after a successful encode, and a failed encode is reported |
| Cover art (`AddCoverToID3V2`, `WriteCoverToFolder`) | on / on | Cover is embedded and also written beside the files |
| Metadata provider (`Internet Options\MetadataPluginUsed`) | `AA545B54-…` = **GD3** | GUID matched in `GD3.dll` in the EAC install folder. `RetrieveCDDBOnUnknownCD` is off |
| Legacy freedb host (`ActualHostName`) | `freedb.freedb.org` | Leftover default, not the active provider |

Drive settings live under `Drive Options\<drive>`. The ripping drive is the
**`HL-DT-STBD-RE  BH16NS40 1.03`** (LG BH16NS40). For that drive: AccurateRip is
on (`UseAccurateRip`), C2 is off (`UseC2Correction`), the combined offset is in
use (`UseCombinedSampleOffset`), and **both `SampleOffset` and
`CombinedSampleOffset` are stored as 0**. AccurateRip's drive-offset list
gives this model as +6 (not re-checked for this capture). Check in EAC (Drive Options > Offset) before relying on
AccurateRip matches. This snapshot only records the stored value. It does not
say which value is right. `SecureMode` (`03,00`) and `ExtractionMode` (`05,00`)
are recorded raw and not decoded here. The `GP65NB60` and `Msft Virtual
DVD-ROM` entries are other drives EAC has seen. They are not used for ripping.

**Redacted (by value name):** `ProxyUserName`, `ProxyUserPassword`,
`EMailUserName`, `EMailHostName` (the freedb/GnuDB submission email),
`ProxyServer`, `ProxyPort`, `UseProxy`, `UseProxyAuthentification`, `ID`
(an EAC install identifier), and `UseRawCDPlayMethod`. That last one is a filter
false positive (it contains "user"). It is a non-secret flag.

## MakeMKV

Config is the registry key. `%APPDATA%\MakeMKV\settings.conf` exists but holds
**only** `app_Key`, so it is not captured. `%USERPROFILE%\.MakeMKV\` holds
`KEYDB.cfg` (a 65 MB disc-key database) and `_private_data.tar`. Both are data,
not settings, and are not captured.

Nearly everything is at MakeMKV's built-in defaults. **No `app_DestinationDir`,
no default selection rule (`app_DefaultSelectionString`), no minimum title
length and no language preference is set**, so MakeMKV's defaults apply and
the output folder is picked per rip in the UI (`C:\Video\<Title>\` by
convention, see [`../README.md`](../README.md)). Title selection is handled
downstream by `build-queue.ps1`, which skips titles under 60 s and takes
`-Exclude` patterns. The one non-default tuning value is
`io_ErrorRetryCount` = 25 (`0x19`). `app_Java` and `app_ccextractor` are empty,
so BD-J menus and closed-caption extraction use whatever MakeMKV bundles.

**Redacted (by value name):** `app_Key` (the registration/beta key), `kslc_…`
(the value name itself contains an identifier, so it is redacted too),
`app_SiteInfoString`, `app_Proxy`, and the drive serial inside the
`speed_HL-DT-ST_BD-RE__BH16NS40_1.03_…` value name. The UI-only `screen_geometry`
and `screen_state` values were dropped.

## MusicBrainz Picard

Picard 3.0.2 reads `%APPDATA%\MusicBrainz\Picard.ini`. Anything not in the file
is at Picard's default. The capture drops the UI-only `[persist]` keys (window
geometry, splitters, column header state).

| Setting | Value | Notes |
| :--- | :--- | :--- |
| `rename_files` | `true` | Files are renamed on save |
| `move_files` | `false` | Files stay in `C:\Rips`. `move_files_to` (`C:/Users/George/Music`) is set but unused |
| `active_file_naming_script_id` | `Preset 1` | The stock "Default file naming script", shown below |
| `save_images_to_tags` / `save_images_to_files` | `true` / `false` | Cover embedded, no separate cover file from Picard |
| `track_ars` | `true` | Track-level artist relationships are loaded |
| `convert_punctuation` | `false` | Curly quotes etc. are kept. `import-music.ps1` handles their UTF-8 byte cost |
| `ca_providers` | Cover Art Archive (release group), Cover Art Archive, URL relationships, Local | Order as stored. The on/off flags sit in a pickled blob and are not decoded here |

`file_renaming_scripts` stores Picard's three stock presets. Preset 1, the
active one, is:

```text
$if2(%albumartist%,%artist%)/
$if(%albumartist%,%album%/,)
$if($gt(%totaldiscs%,1),$if($gt(%totaldiscs%,9),$num(%discnumber%,2),%discnumber%)-,)$if($and(%albumartist%,%tracknumber%),$num(%tracknumber%,2) ,)$if(%_multiartist%,%artist% - ,)%title%
```

**Plugins: none.** `%APPDATA%\MusicBrainz\Picard\plugins3\` and
`%LOCALAPPDATA%\MusicBrainz\Picard\plugins\` are both empty, and neither ini has
an `enabled_plugins` key. `%APPDATA%\MusicBrainz\Picard-2.13.3.ini` is the
pre-upgrade copy left by the 3.x migration. It is not captured. Its `[setting]`
holds only the naming-script keys and `rename_files`.

No AcoustID API key or MusicBrainz login is stored in the file (no such key
names are present).

**Redacted (by key name):** `show_new_user_dialog` (`[setting]`) and
`user_profile_settings` (`[profiles]`). Both matched the `user` filter. Their
values were not read.

## Restoring

Close the tool first. Each one writes its settings on exit.

- **EAC / MakeMKV:** `reg import eac\EAC.reg` / `reg import makemkv\MakeMKV.reg`
  (or double-click). Redacted values are commented out, so the import leaves the
  existing value in place, or the default on a fresh install. Then re-enter
  by hand: the MakeMKV registration key (Help > Register), the EAC metadata
  email (Metadata Options), and any proxy. Check the drive offset (above) on
  the new drive.
- **Picard:** merge the `[setting]` keys into
  `%APPDATA%\MusicBrainz\Picard.ini`, or set them in Options. Copying the file
  over a fresh install also works. Picard rewrites the dropped `[persist]` keys.
  Re-enter the AcoustID key and MusicBrainz login if they were in use.

## Re-capturing

Read-only over SSH (see [`../README.md`](../README.md) for access). Dump the two
registry keys and `Picard.ini` through a name filter that redacts **before**
anything is printed. Diff the result against these files, then grep the diff
for long hex/base64 strings, `@` and `key=` before committing.
