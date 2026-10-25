# OBS Studio guide

Written for OBS Studio 30 and 31 on Windows. The layout is the same on Mac and Linux.

## Basics

OBS records and streams. You build **Scenes** (the layouts you switch between), and each scene holds **Sources** (a game, a window, a camera, a microphone, an image).
The big preview shows the scene. **Controls** at the bottom right have Start Streaming, Start Recording, Settings. **Audio Mixer** shows your sound sources and their levels.

## Do this

### Record your screen for the first time
1. Open **Tools, Auto-Configuration Wizard**, choose **Optimize just for recording**, and finish it. OBS picks the resolution and encoder.
2. In **Sources**, click **+**, choose **Display Capture** (whole screen), name it, **OK**.
3. Click **Start Recording**. Click **Stop Recording** when done.
4. Find the file with **File, Show Recordings**.

### Capture a game
In **Sources**, **+**, **Game Capture**. Set **Mode** to **Capture specific window** and pick the game while it is running. If it stays black, run OBS as administrator, or use **Window Capture** (try **Capture Method: Windows 10**).

### Add a webcam
**+**, **Video Capture Device**, choose the camera. Right-click it in the preview to resize; hold Alt while dragging a corner to crop.

### Set up sound
1. **Settings, Audio**: choose **Desktop Audio** (what you hear) and **Mic/Auxiliary Audio** (your microphone).
2. In the Audio Mixer, keep levels in the green and yellow; red means clipping. Drag the slider down if it peaks red.
3. Click the gear on a mixer track, **Filters**, **+**, add **Noise Suppression** and **Noise Gate** to clean a microphone.
4. To record each source on its own track: **Settings, Output, Recording**, set **Recording Format** to **Matroska (.mkv)** or **Hybrid MP4**, and tick several **Audio Tracks**; then **Advanced Audio Properties** (gear in the mixer) chooses tracks per source.

### Stream to Twitch or YouTube
1. **Settings, Stream**, choose the **Service**, click **Connect Account** (or paste the **Stream Key** from the service's dashboard; never share it).
2. **Settings, Output**: for streaming, pick the encoder (**NVIDIA NVENC**, **AMD**, or **x264** if you have none), a bitrate of about 6000 kbps for 1080p60 on Twitch.
3. Click **Start Streaming**.

### Make the file smaller or better
**Settings, Output**, switch **Output Mode** to **Advanced**. In Recording, pick an encoder; for quality at a small size use **NVENC** with **CQP / CRF** around 20 to 23. Lower numbers are better quality and bigger files.

### Fix choppy video or dropped frames
In the status bar at the bottom: **Dropped frames (network)** means the upload is too slow; lower the bitrate. **Encoding overloaded** means the PC is struggling; use a hardware encoder, lower the resolution in **Settings, Video** (Output scaled), or lower the FPS to 30.

### Add text or an image
**+**, **Text (GDI+)** or **Image**. Layer order follows the Sources list: drag items up to put them on top.

### Switch scenes with keys
**Settings, Hotkeys**: assign a key for each scene, start/stop recording and mute.

### Use the Replay Buffer (save the last 30 seconds)
**Settings, Output, Replay Buffer**, enable it and set the time. Click **Start Replay Buffer**, then press the save hotkey when something great happens.

## When it goes wrong

- **Black screen in Display Capture on a laptop:** Windows is using the other graphics card; in **Settings, System, Display, Graphics** set OBS to **High performance**.
- **Game capture shows black:** start the game first, run both as administrator, disable overlays, or use Window Capture.
- **No audio from a game or app:** add **Application Audio Capture** (Windows 10 2004 and later), or check Desktop Audio is not set to Disabled.
- **Out of sync audio:** Advanced Audio Properties, set a **Sync Offset** (in milliseconds) on the late source.
- **Recording is unplayable or will not open:** record in MKV (it survives a crash), then **File, Remux Recordings** to MP4.
- **Stream disconnects:** use a wired connection; try a closer **Server** in Settings, Stream; lower the bitrate.

## Where to look next

The OBS Knowledge Base at obsproject.com/kb and the OBS forum.
