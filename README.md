# ChatVoice Local — Twitch + Kick chat

This version reads Twitch chat through Twitch's sign-in flow and reads public Kick chat directly from Kick's realtime feed. Kick chat in this version does not use OAuth, webhooks, Wix, or a client secret.

## Sharing this Windows package

Before distributing the ZIP, confirm in the Twitch Developer Console that the registered app used by this package is set to **Public**. The app uses Twitch's device-code flow and its public Client ID; it does not contain a Twitch Client Secret. Each person signs in with and authorizes their own Twitch account. Twitch may ask them to re-authorize later.

Recipients need Windows, Python 3.11 with the Python Launcher, and an internet connection for the first launch. They extract the ZIP and double-click `run.bat`; it creates a local environment and installs the packages. The package does not include anyone's login token, ignored-user list, settings, voice samples, or cloned voice models. Refresh tokens are stored locally using Windows' credential store when available.

The Kick reader uses Kick's current public realtime feed, which is undocumented and may stop working if Kick changes it.

The **Blocked users** page has a separate searchable list of usernames blocked from TTS per platform. Click a username in Activity and confirm to block it only on that platform. Blocked people stay listed in Activity (old and new messages), so you can still delete, time out or ban them; their messages are just not read aloud. When you ban someone from Activity or the OBS dock, their messages are removed from the Activity list. Add a name manually on **Blocked users**, choosing Twitch, Kick, or both. This does not ban anyone on Twitch or Kick.
**Undo last block** reverses the latest addition. Search and unblock a platform/user pair on **Blocked users** to let those messages be read again.
Blocks saved by earlier versions are migrated to both platforms to preserve their previous shared behavior.

On **Advanced Filter**, turn on **Only read highlighted messages (Twitch)** to skip ordinary Twitch messages. Kick has no highlighted-message feature, so this option does not affect Kick chat. Word substitutions replace matching words or phrases only in speech; leave the replacement blank to remove the match. The original messages remain in Activity. Filter and substitution settings are included in the JSON backup.

Use **Pause TTS** on **Text to Speech** to pause speech while keeping chat connected; at most 12 queued messages are kept while paused, with older queued messages dropped first. **Clear speech queue** removes queued messages without stopping the current speech or disconnecting chat. The **Login & Chat** page shows each platform's connection status and has separate reconnect buttons. The **Blocked users** page also exports/imports a JSON backup of channel settings, platform-specific blocked users, and voice presets. Twitch sign-in tokens are not included.

## Emotes

Chat shows emote images, like Chatterino: **7TV**, **BetterTTV** and **FrankerFaceZ** emotes (the global ones plus the ones of the channel you read), **Twitch** emotes and cheermotes, and **Kick** emotes. They appear in **Activity** and in the OBS dock. 7TV zero-width emotes are drawn on top of the emote before them, animated emotes move, and the first lookup of an emote is cached in the `emote_cache` folder next to the program.

Open **Chat setup > Emotes** to switch emotes or single sources on and off, turn animation off, pick the emote size, and choose whether emote names are read aloud (by default they are, as before). Channel emotes load when Twitch or Kick chat connects; **Reload emotes** fetches the lists again. Kick channels are looked up on 7TV by their Kick user id; if a channel has no 7TV set, only the global emotes apply. The emote lists come from 7tv.io, betterttv.net and frankerfacez.com, so they need an internet connection. Emotes need the Pillow package, which `run.bat` installs.

Badges (broadcaster, moderator, VIP, subscriber, founder and a few more) are drawn before the name in Activity and the OBS dock; switch them on or off in the same tab. Twitch badges are the official images, loaded when Twitch chat connects (subscriber badges use the channel's own artwork). A small microphone icon also appears next to people who currently have paid voice time (Bits or Kicks). Kick has no public badge images, so the app draws simple coloured icons for them, except subscriber badges when the channel has its own.

## Dashboard and the Activity page

**Dashboard** shows, for Twitch and Kick: live or offline, viewers watching, uptime, title and category (refreshed every 30 seconds), plus session totals. For Twitch, when you are signed in to your own channel, you can change the stream title and category there (type the category name, press Enter to search, pick it from the list, then press Update). Kick works the same way once its editing permissions are on (see Kick editing below).

**Activity** now has everything in one place: session totals on top (Bits, Kicks, Subs, Gifted subs, Follows, Raids, Points), the chat on the left, and the **event feed** on the right (drag the divider between them to make either wider) (Bits, Kicks, subs, resubs, gifted subs, follows, raids and channel point redemptions, newest first, with a Clear link and a Sample link to preview the layout). Click a name in either list to open their viewer card. Below are Stop audio + clear queue, Clear activity, OBS dock link, and the send box. Bits and Kicks come from chat on either platform. Twitch follows, subs, raids and redemptions need you signed in to Twitch in your own channel. The feed is kept for the current session only.

Twitch permissions added for this: `channel:read:subscriptions`, `channel:read:redemptions`, `channel:manage:broadcast` (title/category) and `moderator:read:followers`. If you were already logged in, log out and log in again once. Until you do, chat and everything else keeps working and the app tells you which events could not start.

## Channel points and Chat controls

**Channel points** lists your rewards and the redemptions waiting for you. You can turn a reward on or off, change its cost, create a new one, and approve or refuse (with a points refund) a waiting redemption. Twitch only lets an app change rewards that this same app created, so rewards you made on twitch.tv are shown as "Twitch only" and stay read-only. Rewards created here show as "You can edit". Channel points need Affiliate or Partner status.

**Chat controls** switches slow mode, followers-only, subscribers-only, emote-only and shield mode, sends an announcement, and can clear chat (with a confirmation). The switches show what Twitch currently has when you open the page. If Twitch refuses a change, they snap back. Twitch only, in your own channel.

The Dashboard also shows your follower count now (Twitch: your own channel; Kick: if Kick provides it).

New Twitch permissions for this: `channel:manage:redemptions`, `moderator:manage:chat_settings`, `moderator:manage:shield_mode`, `moderator:manage:announcements`. Log out and log in to Twitch once to approve them.

## !title and !category

In the **Activity** send box (or the OBS dock chat box), type `!title your new title` or `!category name` and press Send. They are handled inside the app and are never posted to the real Twitch or Kick chat. The dropdown next to the box decides where they apply: **All** (both platforms), **Twitch** or **Kick**. `!category` picks the exact match for the name if there is one, otherwise the closest result, and tells you which category it set. `!title` or `!category` on its own shows the current one. The result appears in the status bar and in the OBS dock notices. Twitch needs you signed in to your own channel. Kick needs the editing permissions set up (see Kick editing, rewards and events).

## Kick editing, rewards and events

Kick has the same title/category editing and channel points management as Twitch, but Kick only allows it when your Kick app has the matching permissions ticked. Do this once:

1. Go to kick.com/settings/developer, open your app and click Edit. Tick **Update channel information**, **Read channel information**, **Read channel points rewards information** and **Read, add, edit and delete channel points rewards on a channel**. Save.
2. In the app, log out of Kick and log in again (Login & Chat). The app always asks Kick for these permissions now. If Kick shows an error at login, one of the boxes in step 1 is not ticked yet.

After that, the Kick card on the Dashboard can change your stream title and category, and the **Channel points** page has a Twitch / Kick switch to turn rewards on or off, change their cost, create rewards and approve or refuse waiting redemptions.

The Activity event feed also shows Kick subs, resubs, gifted subs, reward redemptions and raids/hosts, taken from Kick's live chat feed (the same one that gives Kicks). Kick's format for some of these is not officially documented, so if you see events missing, the first sample of each unfamiliar Kick event is saved in `kick_events.log` next to the program. Kick follows are not available from that feed. Chat controls (slow mode and so on) are Twitch only, because Kick does not offer them to apps.

## Viewer card

Click a name in **Activity** to open that viewer's card. Top: roles (Mod, VIP, Sub...), Twitch account age, how long they have followed you (Twitch, your own channel), paid TTS time, and a private note that is stored only on this PC. Middle: **Mute TTS (app only)** / **Unmute**. Bottom: the real Twitch / Kick actions (delete message, timeout, ban, unban) when you are signed in to your own channel. Close the card with Escape, the Close link, or by clicking elsewhere.

Followage needs one new Twitch permission (`moderator:read:followers`). If you were already logged in, use **Log out / switch account** and log in again once; until then the card shows a short hint instead of the follow date. Kick does not share account age or followers with apps, so Kick cards show roles, paid time, your note and the actions only.

## Using your own Twitch app

The package ships with one default Twitch Client ID. To use your own Twitch application instead, add `"twitch_client_id": "YOUR_CLIENT_ID"` to `connection_settings.json` (for example `{"twitch_client_id": "abc123"}`) and restart. The app must be registered as a **Public** client in the Twitch Developer Console.

## Run

Double-click `run.bat`. On the first launch it creates a local Python environment and installs the listed requirements.

## Program icon

`icon.ico` (Windows) and `icon.png` are used for the window and taskbar icon. To use your own, replace `icon.ico` with a multi-size .ico file (256, 48, 32 and 16 pixels) and restart the program. If you build the .exe, the same icon is used for the file itself. The console window opened by `run.bat` keeps Windows' default icon; the .exe has no console window.

## Optional: build a standalone .exe and an installer

Instead of `run.bat` you can build `ChatVoice Local.exe`, which starts without Python or a console window.

1. **Build the program.** On a Windows PC with Python 3.11, double-click `build_exe.bat`. It installs the packages and PyInstaller, closes any running copy of the program, builds into `dist\ChatVoice Local\`, and then runs a self-check on the result (window system, English and Chinese text engines, audio library). The build only counts as good when the check prints `SELFTEST PASSED`. It takes several minutes and the folder is large (a few GB) because the speech engine is included.
2. **Optional installer.** Install the free [Inno Setup 6](https://jrsoftware.org/isdl.php), then double-click `build_installer.bat`. You get one file, `installer_output\ChatVoiceLocal_Setup_<version>.exe`, that installs the program for the current user (no administrator prompt), adds a Start menu entry and an optional desktop shortcut, and can be installed over an older version. Saved settings, voices and presets are kept when you update or uninstall.
3. **New version.** Change `APP_VERSION` at the top of `main.py`, then repeat steps 1 and 2.

If you use the loose `dist` folder instead of the installer, keep the whole folder together (the settings, saved voices and helper scripts live next to the .exe). Kokoro models download on first use, so the first launch needs internet. The optional voice-cloning setup still needs Python 3.12 installed. The .exe writes any messages it would print to `chatvoice_local.log` next to it; send that file when reporting a problem. If the .exe closes or shows an error, run `build_exe.bat debug` to keep a console window open and read the message.

## Twitch login

1. Open **Login & Chat** and click **Login with Twitch**.
2. Enter the code on Twitch’s activation page and approve chat reading.
3. The app connects to the signed-in account’s own chat. Messages appear in **Activity** and eligible messages are spoken. The green dot appears when the chat feed is connected.

4. The app remembers your sign-in on this PC, so the next launch restores it automatically and shows **Twitch: Logged in as <name>**. To use a different Twitch account, click **Log out / switch account** on **Login & Chat**, then **Login with Twitch**. Logging out forgets the saved sign-in on this PC. On Twitch's activation page, make sure the Twitch account shown in your browser is the one you want, or switch accounts there.

Earlier saved sign-ins may include the old chat sending permission, but this version no longer asks for or uses it.

## Kick chat

Open **Login & Chat**, enter your Kick channel name, and click **Connect Kick chat**. The app looks up the public chatroom and connects to Kick's guest realtime feed; no Kick login or developer secret is needed. The connection automatically retries if it drops. This connection is read-only.

This direct feed is undocumented and may change when Kick updates its website. The official Kick developer API currently documents webhooks for receiving chat events. If Kick changes or blocks the guest feed, chat may stop until the connection code is updated.

### Connect with Kick (login, chat and moderation)

On **Login & Chat**, click **Connect with Kick**, approve in the browser, and you are done: the app connects to your chat and remembers the login. On the **Activity** page, click a Kick name to Delete a message, Timeout (10 min / 1 hour), Ban or Unban on Kick, or just block the name from TTS. Moderation works only for the channel of the signed-in account, which must be the owner or a moderator there. If Kick refuses an action, click **Log out of Kick** and connect again to grant moderation permission.

The program uses one built-in Kick app (Client ID in `kick_auth.py`, override with `"kick_client_id"` in `connection_settings.json`). Its settings at kick.com/settings/developer need the redirect URL `http://localhost:17891/kick/callback` and these scopes ticked: Read user information, Execute moderation actions for moderators, Execute moderation actions on chat messages. The Client Secret is not in the program: the token exchange and refresh go through the Wix function `kickToken` (`post_kickToken`, secret `KICK_CLIENT_SECRET` in Wix Secrets Manager; URL in `kick_auth.py`, override with `"kick_token_relay"`). For your own testing without Wix you can put `"kick_client_secret"` in `connection_settings.json`, which skips the relay; never ship it to others.

To read someone else's public chat without signing in, type their channel name and click **Connect Kick chat**. Reading chat uses Kick's undocumented guest feed and may stop working if Kick changes it.

## Kokoro voices

### Extra Chinese voices (Kokoro v1.1-zh)

The voice selectors also list about 100 extra Mandarin voices (named like `Mandarin Chinese v1.1 — Female 001 (zf_001)`) plus a few English ones (Maple, Sol, Vale) from the separate Kokoro v1.1-zh model. The app looks up the current voice list online on startup and keeps a copy in `zh11_voices.json`, so new voices appear automatically; if you are offline the first time, a short built-in list is shown instead.

The first time you speak with one of these voices, the app downloads that model (about 330 MB). It is loaded alongside the standard model, so memory use goes up while both are in use. The v1.1 model is an early release and is not a strict upgrade over the standard one. v1.1 voices cannot be blended with standard voices (or with each other's other-language voices); blend them only with other v1.1 voices of the same language. English words inside Chinese text are read with the v1.1 English pronunciation. Below them, the entries starting with **Blend:** are ready-made mixes of two of those voices (same language, such as Heart + Bella). They work like any other voice and need no extra download.

**Create Speech** offers Blend voices, Sentence split, Take turns per message, and Take turns per word modes (a single voice is chosen on **Text to Speech**). The mixer shows only the controls used by the selected mode. Type any sentence in **Test text** and press **Test this setup** to hear the current mix; in Blend voices mode, change the Mix % until you like it and press **Save this blend as a voice** to keep it. Saved blends then appear in every voice list as "Saved mix: …". The **Test voice** button on **Text to Speech** plays the voice or voices of the selected chat mode (Twitch and Kick voices, all five voices, or a random pick). Tests wait behind queued chat messages, and are not played while TTS is paused. Take turns per word alternates Kokoro voices on every whitespace-separated word and adds short pauses at punctuation; cloned and Windows SAPI voices are not supported in this mode.

On **Text to Speech**, set one voice for Twitch chat and a separate voice for Kick chat. These choices are saved on this PC and apply to incoming messages on each platform. If **Use five different voices for chatters** is enabled, the chatter's random assigned voice takes priority over the platform voice. The five-voice feature can be turned off to use the one-voice-per-platform setup.

## Random voices

On **Text to Speech**, choose **Random voices** (the fourth button next to Single voice, Twitch + Kick, and Five voices). Every message, including subscription notices, is read by a different randomly picked voice, and the same voice is never used twice in a row. Tick the voice groups that may be picked: by default American and British English. Other languages read English chat poorly, so only enable them if your chat uses that language. The Kokoro v1.1 groups download an extra model on first use, and the Windows voices, blends, saved mixes, and imported packs groups are optional too. If nothing is ticked, the voice from Single voice is used. The selection is saved on this PC and included in the JSON backup. The chatter voices from Five voices are not used in this mode.

## Five voices for chatters

In **Text to Speech**, choose **Five voices** to set up five voices and manage chatter assignments. New chatters are randomly assigned one of the five slots. They can choose their own slot in chat by typing `!Voice1`, `!Voice2`, `!Voice3`, `!Voice4`, or `!Voice5`. Assignments are saved on this PC, separately for Twitch and Kick, and stay the same after restarting. You can also search for a chatter and assign a slot manually. Changing the Text to Speech mode does not delete saved assignments.

## Optional local voice cloning (Qwen3-TTS)

This feature uses the free open-source [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) models and runs entirely on this PC. It needs an NVIDIA graphics card for good speed (it also runs on a CPU, but slowly, and fine-tuning needs the GPU). No paid voice service or account is required. Use your own voice or a recording you have permission to use.

**Install.** In **Create Speech**, click **Install local clone engine** once. The setup window installs a separate Python 3.12 environment, PyTorch with CUDA, Qwen3-TTS, a small speech-to-text helper and the official fine-tuning scripts. It is a large download. The voice model itself (several GB) downloads the first time you use a cloned voice, so that first use is slow.

**Quick cloning.** Choose **Add voice from recording** and select a clear WAV or MP3 of one voice, ideally 5 to 10 seconds of continuous speech without music or background noise. The app trims silence, cuts at a pause if the clip is long, and brings the volume to a normal level. The first time the voice is used, the engine writes down what is said in the clip and keeps it next to the voice file (`custom_voices/clones/<name>.txt`); if the wording is wrong you can correct that file. A correct transcript gives a closer match. Choose the language the generated speech should use (English, Chinese, Japanese, Korean, German, French, Russian, Portuguese, Spanish or Italian).

**Fine-tuning a voice.** For a closer match, use **Fine-tune a voice from ~10 minutes of recordings**. Type a Voice name, pick the recording(s) (about 10 minutes of clear speech in total, one speaker, quiet room, natural variety in tone), and choose the model and training length. A training window opens and shows each step: cutting the recordings into clips, writing down each clip, converting them, training, and adding the voice. When it says Done, click **Refresh voice list** and pick the voice in Create Speech. The 0.6B model is the default because it fits a 12 GB card; the 1.7B model needs more memory. Training stops the cloning engine while it runs. Work files and every training checkpoint are kept in `finetune_work`, so you can delete that folder to save space once you are happy with the voice. Fine-tuning results can vary; if the result is noisy, try a cleaner recording or a different training length.

Cloned and fine-tuned voices can be picked like any other voice (Single voice, Twitch and Kick voices, chatter voices) but cannot be blended or used in per-word turn-taking. They only work in **Take turns per message** mode on Create Speech; adding a cloned voice switches to that mode for you. The speed slider does not change cloned voices.

**Deleting cloned voices.** In Create Speech, click **Delete cloned voices…**, select the quick-cloned or fine-tuned voices you no longer want (Ctrl or Shift to pick several) and confirm. Their recordings, transcripts and trained files are removed from this PC. Anything that used a deleted voice (voice slots, presets, the voice pickers) switches to the standard voice Heart. Standard Kokoro voices, custom voice packs, saved mixes and Windows voices are never listed there and are never touched.

**Speed of cloned voices.** The engine starts and warms up the selected cloned voice in the background when the app opens (and when you pick a cloned voice), so the first message is not slow. Messages are spoken sentence by sentence: the first sentence starts playing while the next ones are still being generated. For roughly twice the speed on a smaller GPU, add `"clone_model": "Qwen/Qwen3-TTS-12Hz-0.6B-Base"` to `connection_settings.json` (a little less accurate than the default 1.7B model). The first time a recording is used, it is transcribed once; the transcript is kept next to it.

**If cloning does not work.** The setup window needs Python 3.12; if it is missing, the setup offers to install it with winget. The setup window shows exactly which step failed and saves a copy as `clone_setup.log`. The engine's own messages are in `qwen3tts.log`. If a cloned voice stays silent, open `last_clone_output.wav` in the app folder (the most recent cloned speech): if you can hear it there, the problem is the Windows sound output; if it is silent, the reference recording is the problem. If you upgraded from the earlier KokoClone version, you can delete the old `.kokoclone-env` and `kokoclone-engine` folders.

## Shortcuts: Ctrl+7, Ctrl+8, Ctrl+9

**Ctrl+7** clears the speech queue, **Ctrl+8** pauses or resumes TTS (waiting messages are held), and **Ctrl+9** mutes the voice reading and returns to normal sound when pressed again. They work from anywhere in Windows, even while the app is in the background, and the Text to Speech page lists them and shows when TTS is paused or muted. Muting stops what is being spoken and clears the waiting messages; while muted, new chat messages are not read and are not kept for later, so nothing is read in a burst when you unmute. Because Ctrl+9 is registered as a global shortcut, other programs will not receive it while this app is running (for example, a browser uses Ctrl+9 to jump to the last tab). If another program already uses Ctrl+9, the shortcut only works while this app is in front.

## Paid voice codes (Bits / Kicks)

Under **Advanced Filter → Paid voices** you can make the voice codes ("Heart: hello", "Adam: hi") a supporter perk.

- **Require payment** is on by default. Switch it off and everybody can use everything for free.
- **What payment unlocks**: *Voice codes only* (unpaid viewers are still read, in the normal voice) or *Everything (reading chat)*: unpaid viewers are not read aloud at all until they pay. Cheering Bits in the same message works, so a viewer's first cheer message is read.
- Set how many **Twitch Bits** and how many **Kick Kicks** buy 30 days. Any amount counts in proportion (double the amount = double the time) and time adds up.
- In *Voice codes only* mode, viewers without paid time are still read aloud, in the normal voice, with the voice code removed. In *Everything* mode they are not read at all (and do not take a Five-voice slot).
- The same cheer or gift is never counted twice, even if Twitch resends the event or Kick repeats a gift. A Kick gift uses the subscriber price when that viewer chatted as a subscriber earlier in the session.
- Twitch: a cheer in your chat unlocks time right away, even in the same message ("Cheer50 Heart: hello").
- Kick: gifted Kicks are read from Kick's live chat feed. Every Kicks event is also written to `kick_kicks_events.log` next to the program so the format can be checked.
- **Reply in chat** thanks the viewer, tells an unpaid viewer how to unlock (at most once every 30 minutes each), and answers `!myvoice` with the time left. It needs you signed in to that platform.
- **Moderators use it for free** and **VIPs use it for free** are separate switches (both off by default, so mods and VIPs pay like everyone else). The role is read from the viewer's chat badge on Twitch and Kick. The broadcaster badge is always free.
- Your own channel account always has access. Use **Give access** to give friends or mods time for free, or **Remove selected** to take it away.
- **Subscribers**: *Pay like everyone* (default), *Free* (subscribers never need paid time) or *Pay less* (subscribers use their own, lower Bits / Kicks prices). The subscriber badge comes with each chat message, like the moderator and VIP badges.
- **Free trial for new viewers**: each viewer can try the paid feature once, for their first messages that use it or for a number of minutes after the first one. `!myvoice` shows what is left.
- **Minimum cheer / minimum gift**: smaller amounts are ignored, so 1-Bit spam does not create tiny bits of time. The Twitch minimum is 10 Bits by default; set 1 to count everything.
- **Maximum stored time**: paid time never grows past this many days from today (365 by default). Time you add by hand is not limited.
- **Expiry reminder**: when a viewer whose time is about to run out chats, the bot tells them once how to keep it. Choose how many days before the end it starts.
- **Free for everyone** (top of the Paid voices tab): the 1 h / 2 h / 4 h buttons give every viewer free access for that long. It stops by itself, or press **Stop**, and it announces the start and the end in chat when *Reply in chat* is on.
- **Add days / Take days off selected** change the time of the viewers selected in the list (uses the Days box). **Export history (CSV)** saves every payment and every change (date, platform, viewer, what, amount, days added), so you can check who paid what.
- **OBS dock**: a payment shows as an alert in the dock, and the **Supporters** button at the top of the dock lists everybody with paid time left and how long. While the free timer runs, the dock shows how much of it is left.
- Paid time is saved in `connection_settings.json`; back it up together with your other settings.

## Blocked words (word block list)

Under **Advanced Filter → Word substitutions → Blocked words** you can keep a list of words that must never be read aloud. The list is a plain text file, `blocked_words.txt`, next to the program (it is created the first time the program starts, and it starts empty).

- Put one word or phrase on each line. Lines that start with `#` are comments. The program reloads the file by itself when you save it; **Reload** does it right away.
- **Open list** opens the file in your text editor. **Import .txt…** adds the words of any other .txt list to it (words that are already there are skipped). Twitch and Kick do not publish their list of banned words, so the program does not come with one: add your own words or import a public list you trust.
- A message that contains a blocked word is not read at all (Activity and the OBS dock still show it). If only the viewer's *name* contains a blocked word, the message is read without the name.
- Every entry is also caught when it is disguised: capitals, stretched letters (`heeeeck`), spaced or dotted letters (`h e c k`, `h.e.c.k`), number and symbol swaps (`h3ck`), look-alike letters from other alphabets (for example a Cyrillic `е`), accents, invisible characters, full-width letters, censored forms (`h**k`, `h*ck`, `he*ck`) and common endings (`s`, `es`, `ed`, `er`, `ing`, `y` ...). Words of 6 or more letters are also caught when glued inside a longer string.
- Special lines: `=word` matches that exact word only; `*word` also catches it inside longer words whatever its length; `-word` is an allowed word that is never blocked (use it when an innocent word looks like a blocked one); a line with spaces is a phrase, which is also caught with the spaces left out.
- No filter is perfect: a very creative spelling can slip through, and a short blocked word can occasionally catch an innocent word (add it as `-word`, or write the blocked one as `=word`). Moderation on the platform itself is still the safety net.
- The on/off switch is saved with your settings. The word list itself is a separate file, so back up `blocked_words.txt` together with `connection_settings.json`.

## Chat Moderation: automatically ban ban evaders (Twitch)

The **Chat Moderation** page holds rules that moderate chat for you while the app runs. Every rule is off until you switch it on.

**Automatically act on ban evaders** uses Twitch's own detection (Suspicious User Detection). When Twitch flags a chatter as a *likely* or *possible* ban evader, the app bans or times them out through the Twitch API, at the moment they type. For each verdict you choose **Do nothing**, **Time out** or **Ban**. Defaults: likely = Ban, possible = Do nothing (Twitch is less sure about "possible", so innocent people can be flagged there).

- **Needs a new permission**: `moderator:read:suspicious_users`. If you logged in before this version, click **Log out of Twitch** and log in again once and approve it. Your Twitch account must be the broadcaster or a moderator of the channel being read.
- **Never read suspected evaders aloud** (on by default): their messages stay in Activity but are not spoken, even with "Do nothing". Twitch-restricted chatters are covered too.
- **Never act on these accounts**: a list of Twitch logins that are never banned or timed out by this rule. Use it for friends and regulars Twitch flags by mistake.
- **Recent automatic actions**: every automatic ban/timeout is listed. Select a row and click **Unban selected** to undo it; that person is also added to the never-act list so they are not banned again on their next message.
- A banned evader disappears from Activity and the OBS dock at once and their waiting speech is dropped.
- Twitch only. Kick has no ban-evader detection in its API.

## !voicelist

When a viewer types **!voicelist** in Twitch or Kick chat, the app replies in chat with every voice code viewers can use, including your own cloned, fine-tuned, custom and saved-mix voices. It answers at most once a minute per platform, needs you signed in to that platform, and is never read aloud. Turn it off with **Answer !voicelist in chat** on the Advanced Filter page. It only works while **Let chat pick a voice with a code** is on.

## Local Windows voices (including IVONA Brian)

The voice selectors also show voices installed in Windows SAPI 5 as `Windows SAPI: <voice name>`. If you have a properly licensed IVONA Brian SAPI 5 voice installed and enabled, select it in **Text to Speech** or as a speaker in **Create Speech**. It runs locally through Windows and does not use the StreamElements endpoint. If no Windows voices appear, install/enable a SAPI 5 voice and restart the app. SAPI voices are spoken as one voice and are not mixed with Kokoro packs.

## Dock links (OBS)
On the **Activity** page there are two buttons. Each one copies a link to the clipboard; in OBS use **Docks > Custom Browser Docks...**, give it a name and paste the link.
- **Dock link Activity**: live chat from Twitch + Kick with the send bar and moderation buttons (the old "OBS dock link").
- **Dock link Feed**: the event feed (Bits, Kicks, subs, gifted subs, follows, raids, channel points) with the session totals on top. Read-only. Which events it shows is set in the app under **Advanced Filter > Feed setup** (the same setting as the Event feed in Activity), so there is nothing to set up in the dock.

Keep both links private. They only work on this PC while the app is running.

## Reward voices (Chat setup > Voice rewards)
Give a channel point reward or a Twitch Custom Power-up its own voice. When a viewer redeems it, the text they typed is read aloud in the voice you picked.
1. In Twitch, turn on "Viewer must enter text" for the reward (or Power-up).
2. In the app open **Chat setup > Voice rewards**, type the exact reward / Power-up name (or pick it from the list), choose a voice (any built-in voice, voice pack, blend or clone), and click **Add / update**. Use `*` to match every redemption.
3. Optional: tick "Say the viewer's name first".
The blocked words list, ignored users and word replacements still apply. Redemptions also show up in the Event feed (Power-ups as POWER-UP).
Custom Power-ups need the Bits permission: log out of Twitch and log in again once to approve it. Twitch only lets you create Power-ups in your Creator Dashboard (the API can read and receive them, not create them).

## Audio output (Voice setup > Audio output)
Choose where the voices play, so you can hear chat while the stream only hears what you want.
- **My speakers / headphones**: your normal output (System default or a device you pick).
- **Stream output**: either a separate device that OBS captures (this needs a virtual audio cable such as the free VB-CABLE), or **ChatVoice Stream Audio**, for people with only one audio output (see below).
- A tick table decides, for **Chat voices** and for **Channel point and Power-up voices**, whether each is heard on your speakers, on the stream, or both. Default: chat on your speakers only, reward voices on both.

OBS setup: install VB-CABLE, pick "CABLE Input" as the Stream output in the app, then in OBS add a source *Audio Output Capture* with device "CABLE Output". Use the Test buttons to check each output.

**One audio output only (no install):** pick **ChatVoice Stream Audio** as the Stream output. The app then starts a small helper window with that name. Voices ticked under "Heard on the stream" are played by that window instead of by the main app, on your normal speakers, so you hear them as usual. In OBS add a source *Application Audio Capture* and choose the window "ChatVoice Stream Audio": OBS gets only those voices as their own source in the audio mixer, and anything you tick only under "Heard on my speakers" never reaches OBS. Keep the helper window open (minimizing it is fine; the close button only minimizes). Use **Show stream audio window** to bring it back. If you tick both columns for the same voices they still play once, from the helper window. Application Audio Capture needs OBS 28 or newer on Windows 10 (version 2004 or later) or Windows 11.
Stop audio, Pause and Mute (Ctrl+9) apply to both outputs.

**Shortcuts and channel point voices:** under the tick table, three boxes let channel point and Power-up voices ignore a shortcut: **Ctrl+7 clear queue** (those waiting voices stay in the queue), **Ctrl+8 pause** (they still play while chat is paused, and the paused chat waits in its original order for you to resume) and **Ctrl+9 mute** (they stay audible, also on the stream). A voice that is already playing is not cut off by a shortcut you ticked. All three are off by default, and chat voices always follow the shortcuts.

## Activity page
- Hover over the page title (or the ⓘ next to it) on Activity and Dashboard to read how the page works.
- The totals at the top (Bits, Kicks, Subs, Gifted subs, Follows, Raids, Points) count one **stream day** and start fresh at 5:00 in the morning, so a stream that runs past midnight stays in one day. They are saved in `feed_today.json`, so closing and reopening the app keeps the day's earnings.
- Hover over **EVENT FEED** to see where each kind of event comes from.
- **Feed setup** (Advanced Filter tab): switch Bits/Kicks/subs, Follows, Raids and Channel points on or off for the Event feed in Activity and in the OBS feed dock. Everything is shown by default. The totals on top keep counting hidden events.

## Start page and accounts
- If you are already logged in (a saved Twitch or Kick login), the app opens on the **Dashboard**. If not, it opens on **Login & Chat** (also reachable from "Chat connections" at the bottom of the sidebar).
- Login & Chat has one row per platform (Twitch, Kick): the button reads **Login**, and becomes **Log out** once you are signed in (a **Reconnect** button appears next to it). To read someone else's public Kick chat without logging in, use the small link under the Kick row.
- Adding another platform later (TikTok, YouTube): add one entry to the `accounts` list in `_build_connection_page` and a branch in `_signed_in`.

## Totals: day vs month
- **Activity** tiles count one stream day (fresh at 5:00 in the morning).
- **Dashboard** tiles are **This month**: Bits, Kicks, Subs, Gifted subs, Follows, Raids and Points add up over the whole month and start again on the 1st. Both are saved in `feed_today.json`. The Clear link in the Event feed only clears the day, never the month.
