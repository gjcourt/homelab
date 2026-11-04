<#
.SYNOPSIS
  AccurateRip check for one ripped CD, as a set of track FLACs. Dot-source it.

.DESCRIPTION
  Answers "is every track of this rip bit-identical to what other people ripped
  from the same pressing?" using the public AccurateRip database. Called by
  import-music.ps1 before staging, so a bad track is found while the disc is
  still in hand - not weeks later, after it went back to the library.

  Why this exists: a library-wide scan on 2026-11-04 found 7 discs whose other
  tracks match AccurateRip at confidence 5-200 but where 1-4 tracks match
  nothing - read errors from scratched discs, all already returned to SFPL.
  Nothing at rip time had caught them, and EAC kept no logs.

  The rip is checked as-is, at shift 0. EAC already applies the drive's read
  offset (homelab #1800: every EAC rip in the sample verified unshifted), so a
  shift search would only find coincidences. #1800's evidence is the reason
  this does NOT try +/-6.

  Decoding uses ffmpeg (already on this box for video). The CRC arithmetic is
  compiled C#: a PowerShell loop over ~200M samples takes minutes per disc.

  Algorithm, cross-checked against media-library's Python checker on the same
  discs:
    * disc ids from track lengths in frames (samples/588), track 1 at LBA 0;
    * ARv1 = sum(sample_i * i), ARv2 = sum(lo32 + hi32 of sample_i * i), both
      mod 2^32, i counted from 1 over the track;
    * the first track skips its first 5 frames (implementations differ by one
      sample, so 2939 and 2940 are both tried), the last skips its last 5;
    * a track's confidence is the best confidence of any pressing (with the
      same track count) whose CRC equals either of ours.

.OUTPUTS
  [pscustomobject] Status, Tracks, Conf (int[] per track), BadTracks (int[]),
  MinConf, Url, Reason. Status is one of:
    OK          every inner track matches
    BAD_TRACKS  some inner tracks match and some do not: re-rip those tracks
    NO_MATCH    the disc is in the database but nothing matches: usually a
                different pressing with the same track layout, not a bad rip
    NOT_IN_AR   the database has no entry for this track layout
    SKIP        not CD audio (sample rate / depth / channels), or not a track rip
    ERROR       decode or network failure; the import is not blocked by it
  "Inner" excludes the first and last track when there are more than two: their
  checksums cover less audio and some submitters' tools disagree at the edges.
#>

if (-not ('ArVerify' -as [type])) {
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;

public static class ArVerify {
    public const int SPF = 588;

    // Decode one FLAC to interleaved 16-bit stereo, one uint per sample (L | R<<16).
    public static uint[] Decode(string ffmpeg, string path) {
        var psi = new ProcessStartInfo(ffmpeg,
            "-v error -nostdin -i \"" + path + "\" -map 0:a:0 -f s16le -acodec pcm_s16le -");
        psi.UseShellExecute = false;
        psi.RedirectStandardOutput = true;
        psi.RedirectStandardError = true;
        psi.CreateNoWindow = true;
        using (var p = Process.Start(psi)) {
            var err = p.StandardError.ReadToEndAsync();
            var ms = new MemoryStream();
            p.StandardOutput.BaseStream.CopyTo(ms);
            p.WaitForExit();
            if (p.ExitCode != 0) throw new Exception("ffmpeg failed on " + path + ": " + err.Result);
            byte[] b = ms.GetBuffer();
            int n = (int)(ms.Length / 4);
            var x = new uint[n];
            Buffer.BlockCopy(b, 0, x, 0, n * 4);
            return x;
        }
    }

    // ARv1/ARv2 CRCs for one track. Returns up to 4 values (2 starts x v1/v2).
    public static uint[] Crcs(uint[] x, bool first, bool last) {
        long n = x.LongLength;
        long end = last ? n - SPF * 5 : n;
        long[] starts = first ? new long[] { SPF * 5 - 1, SPF * 5 } : new long[] { 0 };
        var outv = new List<uint>();
        foreach (long s in starts) {
            ulong v1 = 0, lo = 0, hi = 0;
            unchecked {
                for (long i = 0; i < n; i++) {
                    ulong m = (ulong)(i + 1);
                    if ((long)m < s || (long)m > end) continue;
                    ulong prod = (ulong)x[i] * m;
                    v1 += prod;
                    lo += prod & 0xFFFFFFFFUL;
                    hi += prod >> 32;
                }
            }
            outv.Add((uint)(v1 & 0xFFFFFFFFUL));
            outv.Add((uint)((lo + hi) & 0xFFFFFFFFUL));
        }
        return outv.ToArray();
    }

    public static uint[] DiscIds(long[] frames) {
        long o = 0; var offs = new long[frames.Length];
        for (int i = 0; i < frames.Length; i++) { offs[i] = o; o += frames[i]; }
        long leadout = o, id1 = leadout, id2 = leadout * (frames.Length + 1), n = 0;
        for (int i = 0; i < offs.Length; i++) {
            id1 += offs[i];
            id2 += Math.Max(offs[i], 1) * (i + 1);
            long secs = (offs[i] + 150) / 75; long ds = 0;
            foreach (char c in secs.ToString()) ds += c - '0';
            n += ds;
        }
        long t = (leadout + 150) / 75 - (offs[0] + 150) / 75;
        ulong cddb64 = (((ulong)(n % 255)) << 24) | (((ulong)t) << 8) | (ulong)(uint)frames.Length;
        uint cddb = (uint)(cddb64 & 0xFFFFFFFFUL);
        return new uint[] { (uint)(id1 & 0xFFFFFFFF), (uint)(id2 & 0xFFFFFFFF), cddb };
    }

    public static string Url(long[] frames) {
        uint[] id = DiscIds(frames);
        return string.Format(
            "http://www.accuraterip.com/accuraterip/{0:x}/{1:x}/{2:x}/dBAR-{3:000}-{4:x8}-{5:x8}-{6:x8}.bin",
            id[0] & 0xF, (id[0] >> 4) & 0xF, (id[0] >> 8) & 0xF, frames.Length, id[0], id[1], id[2]);
    }

    // Each pressing: int[track][2] = {confidence, crc}. Empty list = not in the database.
    public static List<long[][]> Lookup(string url) {
        var res = new List<long[][]>();
        byte[] b;
        try {
            using (var wc = new WebClient()) b = wc.DownloadData(url);
        } catch (WebException e) {
            var r = e.Response as HttpWebResponse;
            if (r != null && r.StatusCode == HttpStatusCode.NotFound) return res;
            throw;
        }
        int i = 0;
        while (i + 13 <= b.Length) {
            int cnt = b[i]; i += 13;
            var trk = new long[cnt][];
            for (int k = 0; k < cnt && i + 9 <= b.Length; k++) {
                trk[k] = new long[] { b[i], BitConverter.ToUInt32(b, i + 1) };
                i += 9;
            }
            res.Add(trk);
        }
        return res;
    }
}
'@
}

function Test-AccurateRipDisc {
  <# $Files: the disc's tracks IN TRACK ORDER (FileInfo or paths). #>
  param([Parameter(Mandatory)][object[]]$Files,
        [Parameter(Mandatory)][string]$Ffmpeg,
        [Parameter(Mandatory)][string]$Ffprobe)
  $r = [pscustomobject]@{ Status=''; Tracks=$Files.Count; Conf=@(); BadTracks=@(); MinConf=0; Url=''; Reason='' }
  try {
    $crcs = New-Object System.Collections.Generic.List[object]
    $frames = New-Object System.Collections.Generic.List[long]
    for ($k = 0; $k -lt $Files.Count; $k++) {
      $path = [string]$(if ($Files[$k] -is [IO.FileInfo]) { $Files[$k].FullName } else { $Files[$k] })
      # Match fields by NAME: ffprobe prints them in its own order, not the order asked for.
      $p = @{}
      foreach ($l in (& $Ffprobe -v error -select_streams a:0 -show_entries stream=sample_rate,channels,sample_fmt -of default=noprint_wrappers=1 $path 2>$null)) {
        $i = "$l".IndexOf('='); if ($i -gt 0) { $p["$l".Substring(0, $i)] = "$l".Substring($i + 1) } }
      $fmt = "$($p['sample_rate'])/$($p['sample_fmt'])/$($p['channels'])"
      if ($p['sample_rate'] -ne '44100' -or $p['channels'] -ne '2' -or $p['sample_fmt'] -notmatch '^s16p?$') {
        $r.Status = 'SKIP'; $r.Reason = "not CD audio ($fmt): $([IO.Path]::GetFileName($path))"; return $r }
      $x = [ArVerify]::Decode($Ffmpeg, $path)
      if ($x.LongLength % 588 -ne 0) { $r.Status = 'SKIP'; $r.Reason = "not sector-aligned (not a CD track rip): $([IO.Path]::GetFileName($path))"; return $r }
      $frames.Add([long]($x.LongLength / 588))
      $crcs.Add([ArVerify]::Crcs($x, ($k -eq 0), ($k -eq $Files.Count - 1)))
      $x = $null
    }
    $r.Url = [ArVerify]::Url($frames.ToArray())
    $pressings = [ArVerify]::Lookup($r.Url)
    if ($pressings.Count -eq 0) { $r.Status = 'NOT_IN_AR'; return $r }
    $n = $Files.Count
    $conf = for ($k = 0; $k -lt $n; $k++) {
      $mine = $crcs[$k]; $best = 0
      foreach ($p in $pressings) {
        if ($p.Length -eq $n -and $p[$k] -and ($mine -contains [uint32]$p[$k][1])) { $best = [Math]::Max($best, [int]$p[$k][0]) }
      }
      $best
    }
    $r.Conf = @($conf)
    $inner = $(if ($n -gt 2) { 1..($n - 2) } else { 0..($n - 1) })
    $hit  = @($inner | Where-Object { $r.Conf[$_] -gt 0 })
    $miss = @($inner | Where-Object { $r.Conf[$_] -eq 0 })
    if ($miss.Count -eq 0)    { $r.Status = 'OK' }
    elseif ($hit.Count -gt 0) { $r.Status = 'BAD_TRACKS'; $r.BadTracks = @($miss | ForEach-Object { $_ + 1 }) }
    else                      { $r.Status = 'NO_MATCH' }
    if ($hit.Count -gt 0) { $r.MinConf = ($hit | ForEach-Object { $r.Conf[$_] } | Measure-Object -Minimum).Minimum }
  } catch {
    $r.Status = 'ERROR'; $r.Reason = "$($_.Exception.Message)".Substring(0, [Math]::Min(200, "$($_.Exception.Message)".Length))
  }
  return $r
}
