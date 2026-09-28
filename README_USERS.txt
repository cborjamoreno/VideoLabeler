VIDEO ANNOTATOR — Quick guide
=============================

This tool is for marking in a video when each animal appears (a box) and
what the animals do (actions with a start and an end), and exporting it to
spreadsheets (CSV).

You do not need to install anything.


BEFORE YOU START
----------------
1. Unzip the file you were sent (double-click it).

2. Move the app somewhere you can write to: your Desktop, or a data folder of
   your own. That is where it will save the results.
      - On Mac:     do NOT put it in "Applications".
      - On Windows: do NOT put it in "Program Files".

3. The first time, your system will warn you that the app is not signed. This
   is normal: signing it costs around 300 euros a year. You only have to get
   past the warning once.

      ON MAC
        Right-click VideoLabeler.app -> Open -> Open.
        (Double-clicking will NOT open it the first time.)
        From the second time on, a normal double-click works.

      ON WINDOWS
        Double-click VideoLabeler.exe. If the blue "Windows protected your
        PC" screen appears:
             "More info"  ->  "Run anyway".
        From the second time on it stops asking.
        If your antivirus complains, it is a false positive typical of this
        kind of program: allow it.

4. Be patient the first time it starts: it takes a few seconds to open
   because it unpacks itself. Later launches are faster.


HOW TO USE IT
-------------
1. Press "Open Video" and choose the video.

2. Press "▶ Play" (or the space bar) and watch it. If the video has sound,
   you will hear it; the speaker button mutes it.

3. When something interesting happens, pause.

4. Land on the exact frame:
      «5s  /  5s»    jump 5 seconds        (left and right arrow keys)
      -1f  /  +1f    one exact frame       (Shift + arrow keys)
      Progress bar   click wherever you want to go
   Tip: go back 5 seconds, then step forward frame by frame until the exact
   moment.

5. APPARITION (an animal appears): press "Apparition", click two opposite
   corners of a box around it and pick the species. If you have used it
   before it is in the list: select it, so it is always spelled the same.
   The button stays active to box more animals on that frame; Esc turns it
   off.

6. ACTION (something that lasts): on the frame where it STARTS press
   "Action" and click two corners around it. Choose the type:
      Interaction  (two or more individuals, e.g. two crabs fighting:
                    say how many and the species of each one)
      Passing, Shelter use, Foraging, Presence,
      Approach (moving in on the bait), Consume (eating it)  (one species)
   and tick the species. The button now reads "End action...": go to the
   frame where the action ENDS and click two corners around where it ends
   (the animals may have moved). Done.

   If an animal joins or leaves an interaction while it lasts: go to that
   frame, right-click the interaction and choose "Individual joins..." or
   "Individual leaves...".

   To fix an action later: select it in the list, press Enter to go to it,
   then S on its real first frame (draw the start box again) or E on its
   last frame (draw the end box again).

7. Old files with points: each point becomes an action with no type yet
   ("?" in the list) and a small box. Right-click it -> "Edit action" to
   choose the type, and use S / E to set where it starts and ends.

8. When you are done — or every now and then — press "Save CSVs".


WHERE THE RESULTS GO
--------------------
Into an "annotations" folder created NEXT TO the app (next to the .exe on
Windows), with one subfolder per session: video name plus date and time.
Nothing is overwritten unless you ask for it (see below).

    annotations/
      GX024702_20260729_130450/
        GX024702_bboxes.csv     <- the apparitions
        GX024702_actions.csv    <- the actions (type, species, start, end)
        GX024702_participants.csv  <- each individual of each action, with
                                      when it joined and left

The CSVs open in Excel or LibreOffice. Columns:

  video_name   name of the video file
  frame        frame number (apparitions)
  time_sec     second of the video
  class_name   the species (apparitions)
  action       the action type;  classes  the species, separated by ;
  start_frame, end_frame (and _time_sec)  where the action starts / ends
  duration_sec how long the action lasted
  x, y, width, height   a box: x,y = its top-left corner, in pixels from
               the left / top edge. Actions have a start_ box and an end_
               box. End columns are empty if the end was never marked.


IF YOU MAKE A MISTAKE
---------------------
  Ctrl+Z                     undoes the last mark
  Right-click on a mark      change its species / type, or delete it
  List on the right          select with the arrow keys and press Enter
                             (or double-click) to jump back to that frame;
                             the box above it shows only one kind of mark;
                             select it and press "Delete selected" to remove


USEFUL KEYS
-----------
  Space              play / pause
  Left/Right arrows  5 seconds back / forward
  Shift + arrows     one frame back / forward
  M                  mute the sound
  Ctrl + wheel       zoom on the image (does not affect the coordinates)
  Ctrl+S             save
  Ctrl+Z             undo
  Esc                switch the tool off
  S / E              redraw the start / end box of the selected action


IMPORTANT
---------
Save before closing. If you close with unsaved marks it warns you, but if you
tell it to go ahead, they are lost.

You can pick up where you left off: open the video, press "Load CSVs" and
choose the folder from the previous session (the window opens on the
video's own folder, where annotations shared with the video usually are).
The first time you save after that, the app asks:
  "Overwrite loaded CSVs"  replaces the files you loaded with your version
  "Save to new folder"     keeps them untouched and saves in a new folder
