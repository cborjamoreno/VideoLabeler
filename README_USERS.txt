VIDEO ANNOTATOR — Quick guide
=============================

This tool is for marking the first apparition of each animal in a video, with
a point or with a box, and exporting it to spreadsheets (CSV).

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

3. When an animal appears for the first time, pause.

4. Land on the exact frame:
      «5s  /  5s»    jump 5 seconds        (left and right arrow keys)
      -1f  /  +1f    one exact frame       (Shift + arrow keys)
      Progress bar   click wherever you want to go
   Tip: go back 5 seconds, then step forward frame by frame until the moment
   the animal first becomes visible.

5. Press "Point" (one click on the animal) or "BBox" (two clicks, on two
   opposite corners of the box).

6. Type the species or class name. If you have used it before, it appears in
   the list and you just select it. That way it is always spelled the same.

7. The button stays active: you can keep marking more animals on that same
   frame. Press Esc to switch it off.

8. When you are done — or every now and then — press "Save CSVs".


WHERE THE RESULTS GO
--------------------
Into an "annotations" folder created NEXT TO the app (next to the .exe on
Windows), with one subfolder per session: video name plus date and time.
Nothing is ever overwritten.

    annotations/
      GX024702_20260729_130450/
        GX024702_points.csv     <- the points
        GX024702_bboxes.csv     <- the boxes

The CSVs open in Excel or LibreOffice. Columns:

  video_name   name of the video file
  frame        frame number
  time_sec     second of the video
  class_name   the class you typed
  x, y         position: x = pixels from the left edge,
                         y = pixels from the top edge
  width,height (boxes only) size in pixels; x,y is the top-left corner


IF YOU MAKE A MISTAKE
---------------------
  Ctrl+Z                     undoes the last mark
  Right-click on a mark      change its class, or delete it
  List on the right          double-click to jump back to that frame;
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


IMPORTANT
---------
Save before closing. If you close with unsaved marks it warns you, but if you
tell it to go ahead, they are lost.

You can pick up where you left off: open the video, press "Load CSVs" and
choose the folder from the previous session. Saving will then create a new
folder, so the earlier one stays untouched as a backup.
