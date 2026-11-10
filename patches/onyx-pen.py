#!/usr/bin/env python3
"""Add Onyx raw-drawing ink to termux-x11, in-process.

Applied to a fresh checkout of termux/termux-x11 by the CI workflow. A script
rather than a .patch because it edits by anchor rather than by context, so
upstream drift breaks it loudly in one place instead of silently failing to
apply three hunks.

Why in termux-x11 at all (boox repo, runbook 13): the native Boox note app
draws ink with ZERO DRM frame commits -- SurfaceFlinger reads the digitizer
itself and the EPD paints the stroke. The Qt path cannot reach that; it is
bounded by the panel's 38 Hz tick (26.3 ms) and is already within ~2 ticks of
it. Onyx's TouchHelper binds to a SurfaceView, and LorieView IS one, so the
preview can be drawn by the EPD inside the app already hosting the X session.

The catch this exists to test: raw drawing normally CONSUMES the pen, so X
would stop seeing it. LorieView already has the cure in-process --
sendStylusEvent(), the same native call its own MotionEvent path uses -- so the
points can be fed straight back into X with no socket and no second app.
"""
import pathlib
import sys

root = pathlib.Path(".")


def edit(path, old, new, what):
    p = root / path
    s = p.read_text()
    if new.strip() and new.strip() in s:
        print(f"  = {what}: already applied")
        return
    if s.count(old) != 1:
        sys.exit(f"ANCHOR NOT UNIQUE in {path} ({s.count(old)}x): {what}")
    p.write_text(s.replace(old, new, 1))
    print(f"  + {what}")


# 1. The SDK lives in Boox's own maven, which is http-only.
edit("build.gradle",
     """        maven { url "https://jitpack.io" }""",
     """        maven { url "https://jitpack.io" }
        maven { url "http://repo.boox.com/repository/maven-public/"; allowInsecureProtocol = true }""",
     "boox maven repository")

# 2. The pen SDK itself.
edit("lorie/build.gradle",
     """    implementation 'androidx.preference:preference:1.2.1'""",
     """    implementation 'androidx.preference:preference:1.2.1'
    // The SDK still declares the pre-AndroidX support library, whose classes
    // duplicate androidx.core and break :lorie-app's shared-UID duplicate
    // check (two APKs here share a UID, so that check is strict):
    //   Duplicate class android.support.v4.app.INotificationSideChannel found in
    //   core-1.13.1.aar and support-compat-24.2.1.aar
    // Nothing used from the pen SDK needs it.
    implementation('com.onyx.android.sdk:onyxsdk-pen:1.5.4.4') {
        exclude group: 'com.android.support'
    }""",
     "onyxsdk-pen dependency")

# 2b. The SDK's manifest declares application-level attributes of its own
# (allowBackup=true, label=@string/app_name) which collide with lorie's. Tell
# the merger ours win -- an SDK should not be relabelling the host app anyway.
# Both lorie's manifest and lorie-app's sharedUid flavour need it, since the
# shared-UID variant merges the library in too.
for manifest, anchor in (
    ("lorie/src/main/AndroidManifest.xml", "    <application\n"),
    ("lorie-app/src/sharedUid/AndroidManifest.xml", "    <application\n"),
):
    p = root / manifest
    if not p.exists():
        print(f"  ! {manifest} missing, skipped")
        continue
    s = p.read_text()
    if 'tools:replace' in s:
        print(f"  = tools:replace already in {manifest}")
        continue
    if "xmlns:tools" not in s:
        s = s.replace("<manifest ", '<manifest xmlns:tools="http://schemas.android.com/tools" ', 1)
    if s.count(anchor) < 1:
        sys.exit(f"ANCHOR NOT FOUND in {manifest}: <application")
    s = s.replace(anchor, '    <application\n        tools:replace="android:allowBackup,android:label"\n', 1)
    p.write_text(s)
    print(f"  + tools:replace in {manifest}")

# 3. The hook.
(root / "lorie/src/main/java/com/termux/x11/OnyxPen.java").write_text('''package com.termux.x11;

import android.graphics.Rect;
import android.util.Log;

import com.onyx.android.sdk.data.note.TouchPoint;
import com.onyx.android.sdk.pen.RawInputCallback;
import com.onyx.android.sdk.pen.TouchHelper;
import com.onyx.android.sdk.pen.data.TouchPointList;

import java.util.ArrayList;

/**
 * Low-latency ink for Onyx Boox devices.
 *
 * TouchHelper hands the stroke to the e-ink controller, which paints it from
 * the kernel's handwriting buffer without the app producing a frame at all --
 * measured on a Note Air 4C as literally zero DRM frame commits, against ~3
 * frames and ~59 ms per sample for the same stroke drawn through Qt.
 *
 * Raw drawing consumes the pen, so X would otherwise go blind. Every point is
 * therefore fed back through LorieView.sendStylusEvent(), the same native call
 * the ordinary MotionEvent path uses, which keeps X clients seeing the pen
 * exactly as before. Pressure is rescaled from the SDK's 0..4095 to the 0..65535
 * that path expects; tilt is zeroed because TouchPoint does not carry it.
 *
 * Every SDK call is guarded: on a non-Onyx device TouchHelper.create() throws,
 * and the app must carry on as a perfectly ordinary X server.
 */
public final class OnyxPen {
    private static final String TAG = "OnyxPen";
    private static final float PRESSURE_SCALE = 65535.0f / 4095.0f;

    private static TouchHelper helper;
    private static LorieView view;

    private OnyxPen() {
    }

    public static void attach(final LorieView v) {
        if (helper != null || v == null) {
            return;
        }
        view = v;
        // post(): the surface has no size until it has been laid out, and
        // setLimitRect() of an empty rect silently scribbles nowhere.
        v.post(new Runnable() {
            @Override
            public void run() {
                try {
                    Rect limit = new Rect(0, 0, v.getWidth(), v.getHeight());
                    helper = TouchHelper.create(v, callback)
                            .setStrokeWidth(3.0f)
                            .setLimitRect(limit, new ArrayList<Rect>())
                            .openRawDrawing();
                    helper.setRawDrawingEnabled(true);
                    Log.i(TAG, "raw drawing enabled, limit=" + limit);
                } catch (Throwable t) {
                    Log.i(TAG, "unavailable, carrying on without it: " + t);
                    helper = null;
                }
            }
        });
    }

    public static void detach() {
        try {
            if (helper != null) {
                helper.setRawDrawingEnabled(false);
                helper.closeRawDrawing();
            }
        } catch (Throwable t) {
            Log.i(TAG, "close failed: " + t);
        } finally {
            helper = null;
            view = null;
        }
    }

    private static void forward(TouchPoint p, boolean down) {
        LorieView v = view;
        if (v == null || p == null) {
            return;
        }
        try {
            v.sendStylusEvent(p.x, p.y,
                    (int) (p.pressure * PRESSURE_SCALE),
                    0, 0, 0,
                    down ? 1 : 0,
                    false, false);
        } catch (Throwable t) {
            Log.i(TAG, "forward failed: " + t);
        }
    }

    private static final RawInputCallback callback = new RawInputCallback() {
        @Override
        public void onBeginRawDrawing(boolean b, TouchPoint p) {
            forward(p, true);
        }

        @Override
        public void onEndRawDrawing(boolean b, TouchPoint p) {
            forward(p, false);
        }

        @Override
        public void onRawDrawingTouchPointMoveReceived(TouchPoint p) {
            forward(p, true);
        }

        @Override
        public void onRawDrawingTouchPointListReceived(TouchPointList l) {
        }

        @Override
        public void onBeginRawErasing(boolean b, TouchPoint p) {
        }

        @Override
        public void onEndRawErasing(boolean b, TouchPoint p) {
        }

        @Override
        public void onRawErasingTouchPointMoveReceived(TouchPoint p) {
        }

        @Override
        public void onRawErasingTouchPointListReceived(TouchPointList l) {
        }
    };
}
''')
print("  + OnyxPen.java")

# 4. Wire it up.
edit("lorie/src/main/java/com/termux/x11/MainActivity.java",
     "        setContentView(R.layout.main_activity);",
     """        setContentView(R.layout.main_activity);
        OnyxPen.attach(getLorieView());""",
     "OnyxPen.attach() in onCreate")

print("onyx-pen patch applied")
