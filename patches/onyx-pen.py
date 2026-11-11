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
    implementation('com.onyx.android.sdk:onyxsdk-pen:1.5.9') {
        exclude group: 'com.android.support'
    }""",
     "onyxsdk-pen dependency")

# 2b. The SDK's manifest sets application attributes of its own
# (allowBackup=true, label=@string/app_name) which collide with the host's.
# tools:replace only works where the attribute is actually DECLARED -- naming
# android:label on lorie's <application>, which does not declare it, fails with
# "tools:replace specified for attribute android:label, but no new value
# specified". So each replace goes where that attribute lives:
#   lorie/src/main      declares android:allowBackup="false"
#   lorie-app/src/main  declares android:label="@string/lorie_app_name"
for manifest, anchor, replacement in (
    ("lorie/src/main/AndroidManifest.xml",
     "    <application\n        android:name=\".LorieApp\"",
     '    <application\n        tools:replace="android:allowBackup"\n        android:name=".LorieApp"'),
    ("lorie-app/src/main/AndroidManifest.xml",
     "    <application\n        android:icon=",
     '    <application\n        tools:replace="android:label"\n        android:icon='),
):
    p = root / manifest
    if not p.exists():
        sys.exit(f"MISSING {manifest}")
    s = p.read_text()
    if "tools:replace" in s:
        print(f"  = tools:replace already in {manifest}")
        continue
    if "xmlns:tools" not in s:
        s = s.replace("<manifest ", '<manifest xmlns:tools="http://schemas.android.com/tools" ', 1)
    if s.count(anchor) != 1:
        sys.exit(f"ANCHOR NOT UNIQUE in {manifest} ({s.count(anchor)}x)")
    s = s.replace(anchor, replacement, 1)
    p.write_text(s)
    print(f"  + tools:replace in {manifest}")

# 2c. Three of the SDK's transitive artifacts each ship their own copy of
# lib/arm64-v8a/libc++_shared.so (onyxsdk-pen, onyxsdk-pennative, mmkv), and
# the application module refuses to merge them:
#   3 files found with path 'lib/arm64-v8a/libc++_shared.so'
# They are the same C++ runtime; take the first. This is the collision
# onyx-intl/OnyxAndroidDemo#144 describes for 1.5.4.
edit("lorie-app/build.gradle",
     """android {
    namespace 'com.termux.x11.app'""",
     """android {
    packagingOptions {
        pickFirst '**/libc++_shared.so'
    }
    namespace 'com.termux.x11.app'""",
     "pickFirst libc++_shared.so")

# 2d. R8 refuses to minify with classes it cannot resolve. MMKV (pulled in by
# the pen SDK) references the pre-AndroidX support ANNOTATIONS, which 2's
# exclusion removed along with support-compat:
#   Missing class android.support.annotation.NonNull (referenced from
#   com.tencent.mmkv.MMKVContentProvider.call and 30 other contexts)
# They are compile-time only, so nothing needs them at runtime -- but R8 has to
# be told. The keep rule is separate: the SDK reaches its own classes
# reflectively, and minification would otherwise be free to rename them.
rules = root / "lorie-app/proguard-rules.pro"
addition = """

# --- Onyx pen SDK (added by patches/onyx-pen.py) ---
-dontwarn android.support.**
-dontwarn com.onyx.**
-keep class com.onyx.** { *; }
-keep class com.tencent.mmkv.** { *; }
"""
if "Onyx pen SDK" in rules.read_text():
    print("  = proguard rules already present")
else:
    rules.write_text(rules.read_text() + addition)
    print("  + proguard rules for the SDK")

# 2e. Turn minification off for this debug build.
#
# R8 refuses to run while any referenced class is unresolvable, and the pen SDK
# references a pile it does not ship or need: the support annotations (removed
# by 2's exclusion), then androidx.cardview, androidx.databinding and
# androidx.window.extensions. Adding -dontwarn for each is whack-a-mole against
# a list R8 only reveals one build at a time, and minification buys an
# experimental build nothing -- only a smaller APK and renamed symbols that
# make the SDK's reflective lookups riskier.
edit("lorie-app/build.gradle",
     """        minifyEnabled true
        shrinkResources true""",
     """        minifyEnabled false
        shrinkResources false""",
     "minification off for debug")

# 3. The hook.
#
# Two mechanisms live here, chosen at RUNTIME so one build can A/B them:
#
#   settings put global x11_onyxpen_mode eac   (default) -- the firmware's own
#       EACScreenNoteManager, handed the view its config names
#   settings put global x11_onyxpen_mode sdk   -- Onyx's pen SDK, TouchHelper
#       with FEATURE_SF_TOUCH_RENDER
#   settings put global x11_onyxpen_mode off   -- neither; stock behaviour
#
# `eac` is the default because the firmware told us, in its own words, that it
# is already most of the way there. See runbook 13 4p.
(root / "lorie/src/main/java/com/termux/x11/OnyxPen.java").write_text('''package com.termux.x11;

import android.content.Context;
import android.graphics.Rect;
import android.provider.Settings;
import android.util.Log;
import android.view.MotionEvent;
import android.view.View;

import com.onyx.android.sdk.data.note.TouchPoint;
import com.onyx.android.sdk.pen.RawInputCallback;
import com.onyx.android.sdk.pen.TouchHelper;
import com.onyx.android.sdk.pen.data.TouchPointList;

import java.lang.reflect.Method;
import java.util.ArrayList;

/**
 * Low-latency ink for Onyx Boox devices.
 *
 * The native note app paints ink with ZERO DRM frame commits -- measured on a
 * Note Air 4C, against ~3 frames and ~59 ms per sample for the same stroke
 * drawn through Qt. The e-ink controller paints the stroke; the app never
 * produces a frame for it.
 *
 * MODE "eac" -- the firmware's own path
 * ------------------------------------
 * This device's framework is patched: `android.view.View.dispatchTouchEvent`
 * calls `EACScreenNoteManager.onDispatchMotionEvent(view, event)`, and EAC
 * accelerates handwriting on any app whose EAC config names the drawing view
 * (`globalActivityConfig.noteConfig.drawViewKey`; six packages ship one --
 * OneNote, Evernote, WPS...). Ours already names `com.termux.x11.LorieView`,
 * and with `sys.oec.service.debug` on, EAC is visibly doing its half:
 *
 *     startEACScreenNote, limitRegion: Rect(0, 0 - 2480, 1860)
 *     setScreenHandWritingRegionLimit: com.termux.x11.LorieView{...}
 *     setScreenHandWritingPenState: PEN_START
 *
 * and then refusing on every sample:
 *
 *     onDispatchMotionEventImpl, View: android.widget.FrameLayout{app:id/frame}
 *     onStylusEventOutsideDrawViewImpl
 *     setScreenHandWritingPenState: PEN_PAUSE
 *
 * Because termux-x11 puts its touch listener on the PARENT
 * (`lorieParent.setOnTouchListener`, MainActivity), the parent is the touch
 * target, so the framework hook fires with the FrameLayout. EAC matches that
 * against `drawViewKey`, finds no match, calls the stroke "outside draw view"
 * and PAUSES handwriting -- once per sample.
 *
 * So this mode hands the same event back to EAC attributed to the LorieView,
 * which is the view its own config asks for. No EPD call of our own: EAC owns
 * the pen state, the region and the post-stroke repaint, and it is the code
 * Onyx ships for exactly this job.
 *
 * MODE "sdk" -- Onyx's pen SDK
 * ----------------------------
 * `TouchHelper` with `FEATURE_SF_TOUCH_RENDER`, kept for comparison. Note the
 * two-argument `create(view, callback)` selects `FEATURE_APP_TOUCH_RENDER`,
 * where the SDK captures the pen and the APP must paint it -- which is why an
 * earlier build saw points arrive and the panel never change scheme. Raw
 * drawing consumes the pen, so points are fed back through
 * `LorieView.sendStylusEvent()` to keep X clients seeing it.
 *
 * Every call is guarded. On a non-Onyx device the SDK and the framework
 * classes are both absent, and the app must carry on as an ordinary X server.
 */
public final class OnyxPen {
    private static final String TAG = "OnyxPen";
    private static final String MODE_SETTING = "x11_onyxpen_mode";
    private static final float PRESSURE_SCALE = 65535.0f / 4095.0f;
    private static final float STROKE_WIDTH = 3.0f;
    // Long enough not to fire between the strokes of one character, short
    // enough to feel immediate. Only used in "sdk" mode; in "eac" mode the
    // firmware uses its own noteConfig.repaintLatency (500 ms here).
    private static final int PEN_UP_REFRESH_MS = 400;

    private static TouchHelper helper;
    private static LorieView view;
    private static String mode = "eac";

    // EACScreenNoteManager, reached reflectively: it is framework-internal, so
    // there is nothing to compile against. Resolved once.
    private static int moves;
    private static boolean pushPoints;
    private static java.lang.reflect.Method mMoveTo, mAddPoint, mFinishStroke, mPenUp;
    private static int reapplyEvery;
    private static Object eacManager;
    private static Method eacOnDispatchMotionEvent;

    private OnyxPen() {
    }

    public static void attach(final LorieView v) {
        if (view != null || v == null) {
            return;
        }
        view = v;
        mode = readMode(v.getContext());
        Log.i(TAG, "mode=" + mode);
        if ("eac".equals(mode)) {
            bindEac();
        } else if ("sdk".equals(mode)) {
            attachSdk(v);
        }
    }

    /**
     * Should the LorieView itself be the touch target?
     *
     * Only in "eac" mode -- "off" has to stay stock, or the null arm of every
     * measurement stops being a null arm.
     */
    public static boolean wantsViewTouchTarget(Context ctx) {
        // Both real modes want it, for the same reason: it is what makes the
        // framework pass EAC the view our config names, and EAC's
        // allowApplyTransientUpdate is what suppresses the refresh-on-touch
        // whose DRM commit ends handwriting mode. "sdk" needs that suppression
        // just as much -- measured, sdk mode without it took 4 frames per
        // stroke and painted nothing.
        String m = readMode(ctx);
        return "eac".equals(m) || "sdk".equals(m);
    }

    private static String readMode(Context ctx) {
        try {
            // Settings.Global is public API, deliberately: this has to be
            // switchable from adb without a rebuild, and a system property
            // would need hidden-API reflection to read.
            String s = Settings.Global.getString(ctx.getContentResolver(), MODE_SETTING);
            if (s != null && !s.isEmpty()) {
                return s.trim();
            }
        } catch (Throwable t) {
            Log.i(TAG, "mode lookup failed, defaulting: " + t);
        }
        return "eac";
    }

    /**
     * Framework knobs for the SurfaceFlinger handwriting path, each driven by
     * a Settings.Global value so they can be swept from adb with no rebuild:
     *
     *   settings put global x11_onyx_updlist 1     # ViewUpdateHelper.setUpdListSize
     *   settings put global x11_onyx_brush   1     # setBrushRawDrawingEnabled
     *   settings put global x11_onyx_trigger 1     # setTrigger
     *   settings put global x11_onyx_refresh 4     # EInkHelper.setAppScopeRefreshMode
     *   settings delete global x11_onyx_updlist    # leave the firmware default
     *
     * Why these exist: measured, SF paints our strokes at exactly ONE
     * ebc_ioctl HANDWRITE per 20 pen samples -- scaling with sample count, not
     * time (40/80/160 samples -> 2/4/8 paints; the same 40 samples stretched
     * over 2 s still gives 2). The stock Boox canvas gets ~6 paints PER
     * sample. Something is batching ours by 20, and setUpdListSize is the
     * candidate the name points at.
     *
     * An unset value means "do not call at all", so the default build changes
     * nothing until a knob is deliberately turned.
     */
    private static void applyTunables(Context ctx) {
        callInt(ctx, "x11_onyx_updlist", "android.onyx.ViewUpdateHelper", "setUpdListSize", int.class);
        callInt(ctx, "x11_onyx_trigger", "android.onyx.ViewUpdateHelper", "setTrigger", int.class);
        callInt(ctx, "x11_onyx_brush", "android.onyx.ViewUpdateHelper", "setBrushRawDrawingEnabled", boolean.class);
        callInt(ctx, "x11_onyx_autosync", "android.onyx.ViewUpdateHelper", "setAutoSyncBufEnable", boolean.class);
        callInt(ctx, "x11_onyx_regionmode", "android.onyx.ViewUpdateHelper", "setScreenHandWritingRegionMode", int.class);
        callInt(ctx, "x11_onyx_refresh", "android.onyx.optimization.EInkHelper", "setAppScopeRefreshMode", int.class);
        callInt(ctx, "x11_onyx_strokestyle", "android.onyx.ViewUpdateHelper", "setStrokeStyle", int.class);
        // The measured difference against the native canvas is the TCON
        // display mode: it runs display_mode[1] and completes a handwriting
        // update in ~2.7 ms; we run display_mode[4] and take ~85 ms, which is
        // the entire remaining gap. These are the candidate controls.
        callInt(ctx, "x11_onyx_scheme", "android.onyx.ViewUpdateHelper", "setDisplayScheme", int.class);
        callInt(ctx, "x11_onyx_turbo", "android.onyx.ViewUpdateHelper", "setEpdTurbo", int.class);
        callInt(ctx, "x11_onyx_bwmode", "android.onyx.ViewUpdateHelper", "setBWMode", int.class);
        callInt(ctx, "x11_onyx_grayscale", "android.onyx.ViewUpdateHelper", "setGrayscaleMode", int.class);
        callInt(ctx, "x11_onyx_gcinterval", "android.onyx.ViewUpdateHelper", "setGcRefreshInterval", int.class);
        callFloat(ctx, "x11_onyx_strokewidth", "android.onyx.ViewUpdateHelper", "setStrokeWidth");
    }

    private static void callFloat(Context ctx, String key, String cls, String method) {
        String raw;
        try {
            raw = Settings.Global.getString(ctx.getContentResolver(), key);
        } catch (Throwable t) {
            return;
        }
        if (raw == null || raw.trim().isEmpty()) {
            return;
        }
        try {
            Class.forName(cls).getMethod(method, float.class)
                    .invoke(null, Float.valueOf(Float.parseFloat(raw.trim())));
            Log.i(TAG, key + "=" + raw.trim() + " -> " + method);
        } catch (Throwable t) {
            Log.i(TAG, key + " failed: " + t);
        }
    }

    private static int intSetting(Context ctx, String key, int dflt) {
        try {
            String raw = Settings.Global.getString(ctx.getContentResolver(), key);
            return (raw == null || raw.trim().isEmpty()) ? dflt : Integer.parseInt(raw.trim());
        } catch (Throwable t) {
            return dflt;
        }
    }

    private static void callInt(Context ctx, String key, String cls, String method, Class<?> type) {
        String raw;
        try {
            raw = Settings.Global.getString(ctx.getContentResolver(), key);
        } catch (Throwable t) {
            return;
        }
        if (raw == null || raw.trim().isEmpty()) {
            return;                       // unset: leave the firmware default
        }
        try {
            int v = Integer.parseInt(raw.trim());
            Object arg = (type == boolean.class) ? (Object) Boolean.valueOf(v != 0) : (Object) Integer.valueOf(v);
            Class.forName(cls).getMethod(method, type).invoke(null, arg);
            Log.i(TAG, key + "=" + v + " -> " + method);
        } catch (Throwable t) {
            Log.i(TAG, key + " failed: " + t);
        }
    }

    /**
     * Bind the framework's point-push calls.
     *
     * Measured (runbook 13 4r): with EAC driving, SurfaceFlinger paints our
     * stroke at exactly ONE rectangle per 20 pen samples -- independent of
     * distance and of time, so it is a hard decimation, not a timer. The
     * stock Boox canvas gets ~6 paints PER sample, because its SDK PUSHES
     * every point to the controller rather than leaving SF to sample the
     * digitizer on its own.
     *
     * These are the framework calls that SDK wraps. EAC already owns the pen
     * state, the region and the refresh suppression, so this adds only the
     * points -- and deliberately makes NO pen-state call, which is what froze
     * the panel three times (4o).
     */
    private static void bindPointPush() {
        try {
            Class<?> c = Class.forName("android.onyx.ViewUpdateHelper");
            mMoveTo = c.getMethod("moveTo", View.class, float.class, float.class, float.class);
            mAddPoint = c.getMethod("addStrokePoint", float.class, float.class, float.class,
                    float.class, float.class, float.class);
            mFinishStroke = c.getMethod("finishStroke", float.class, float.class, float.class,
                    float.class, float.class, float.class);
            mPenUp = c.getMethod("penUp");
            Log.i(TAG, "point push bound");
        } catch (Throwable t) {
            Log.i(TAG, "point push unavailable: " + t);
            mMoveTo = mAddPoint = mFinishStroke = mPenUp = null;
        }
    }

    private static void push(MotionEvent e) {
        LorieView v = view;
        if (v == null || mAddPoint == null) {
            return;
        }
        // The LorieView is positioned at 0,0 and fills the screen, so view
        // coordinates are screen coordinates and need no mapping.
        float x = e.getX(), y = e.getY();
        float pressure = e.getPressure() * 4095.0f;    // MotionEvent 0..1 -> SDK 0..4095
        float size = e.getSize();
        float ts = (float) e.getEventTime();
        try {
            switch (e.getAction()) {
                case MotionEvent.ACTION_DOWN:
                    mMoveTo.invoke(null, v, x, y, STROKE_WIDTH);
                    break;
                case MotionEvent.ACTION_MOVE:
                    mAddPoint.invoke(null, STROKE_WIDTH, x, y, pressure, size, ts);
                    break;
                case MotionEvent.ACTION_UP:
                case MotionEvent.ACTION_CANCEL:
                    mFinishStroke.invoke(null, STROKE_WIDTH, x, y, pressure, size, ts);
                    mPenUp.invoke(null);
                    break;
                default:
                    break;
            }
        } catch (Throwable t) {
            Log.i(TAG, "point push failed, disabling: " + t);
            mAddPoint = null;
        }
    }

    /**
     * Pen-down hook. Re-applies the tunables because EAC sets its own stroke
     * parameters when it enters handwriting mode, which may land after the
     * ones applied at attach time.
     */
    public static void onStrokeStart(MotionEvent e) {
        LorieView v = view;
        if (v == null || e == null) {
            return;
        }
        if (e.getToolType(0) != MotionEvent.TOOL_TYPE_STYLUS
                && e.getToolType(0) != MotionEvent.TOOL_TYPE_ERASER) {
            return;
        }
        if (pushPoints) {
            push(e);
        }
        // On DOWN, and then on every Nth MOVE.
        //
        // Applying once on DOWN measured as having no effect at all, and the
        // likeliest reason is ordering: EAC runs applyStrokeParam when it
        // enters handwriting mode, from inside the framework's own
        // dispatchTouchEvent hook, so whatever it sets lands after whatever we
        // set. Re-applying mid-stroke is the way to find out whether these
        // knobs are inert or merely overwritten.
        //
        // x11_onyx_reapply = 0/unset -> DOWN only; N -> also every Nth move.
        if (e.getAction() == MotionEvent.ACTION_DOWN) {
            moves = 0;
            applyTunables(v.getContext());
            return;
        }
        if (e.getAction() != MotionEvent.ACTION_MOVE) {
            return;
        }
        int every = reapplyEvery;
        if (every > 0 && (++moves % every) == 0) {
            applyTunables(v.getContext());
        }
    }

    private static void bindEac() {
        try {
            Class<?> c = Class.forName("android.onyx.optimization.EACScreenNoteManager");
            eacManager = c.getMethod("sharedInstance").invoke(null);
            eacOnDispatchMotionEvent =
                    c.getMethod("onDispatchMotionEvent", View.class, MotionEvent.class);
            Log.i(TAG, "EACScreenNoteManager bound");
            reapplyEvery = intSetting(view.getContext(), "x11_onyx_reapply", 0);
            pushPoints = intSetting(view.getContext(), "x11_onyx_push", 0) != 0;
            if (pushPoints) {
                bindPointPush();
            }
            applyTunables(view.getContext());
        } catch (Throwable t) {
            Log.i(TAG, "no EACScreenNoteManager, carrying on without it: " + t);
            eacManager = null;
            eacOnDispatchMotionEvent = null;
        }
    }

    /**
     * Called from the parent's touch listener with the event the parent got.
     *
     * Stylus only. EAC's own hook already reports every event against the
     * parent view, and finger events there are correct -- the pen is the only
     * thing being re-attributed, and re-reporting touches would fight EAC's
     * finger handling for no gain.
     *
     * Returns nothing and consumes nothing: the framework's own signature is
     * `void`, so this cannot swallow an event that X needs.
     */
    public static void onMotion(MotionEvent e) {
        LorieView v = view;
        Method m = eacOnDispatchMotionEvent;
        if (v == null || m == null || e == null) {
            return;
        }
        if (e.getToolType(0) != MotionEvent.TOOL_TYPE_STYLUS
                && e.getToolType(0) != MotionEvent.TOOL_TYPE_ERASER) {
            return;
        }
        try {
            m.invoke(eacManager, v, e);
        } catch (Throwable t) {
            // Once, not per sample: at 80 events a second a broken call would
            // otherwise bury the log that diagnoses it.
            if (eacOnDispatchMotionEvent != null) {
                Log.i(TAG, "EAC dispatch failed, disabling: " + t);
                eacOnDispatchMotionEvent = null;
            }
        }
    }

    private static void attachSdk(final LorieView v) {
        // post(): the surface has no size until it has been laid out, and
        // setLimitRect() of an empty rect silently scribbles nowhere.
        v.post(new Runnable() {
            @Override
            public void run() {
                try {
                    Rect limit = new Rect(0, 0, v.getWidth(), v.getHeight());
                    // FEATURE_SF_TOUCH_RENDER, not the default: see the class
                    // comment. SFTouchRender drives EpdPenManager and
                    // leaveScribbleMode itself.
                    helper = TouchHelper.create(v, TouchHelper.FEATURE_SF_TOUCH_RENDER, callback)
                            .setStrokeWidth(STROKE_WIDTH)
                            .setLimitRect(limit, new ArrayList<Rect>())
                            .openRawDrawing();
                    helper.setRawDrawingEnabled(true);
                    helper.setRawDrawingRenderEnabled(true);
                    helper.setPenUpRefreshEnabled(true);
                    helper.setPenUpRefreshTimeMs(PEN_UP_REFRESH_MS);
                    Log.i(TAG, "raw drawing enabled (SF render), limit=" + limit);
                } catch (Throwable t) {
                    Log.i(TAG, "SDK unavailable, carrying on without it: " + t);
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
            eacManager = null;
            eacOnDispatchMotionEvent = null;
        }
    }

    // NO EpdController CALLS ANYWHERE IN THIS CLASS, on purpose. Three builds
    // drove the pen state by hand and all three had to be rolled back:
    //
    //   PEN_START at startup                  -> panel frozen (liveness 0 px)
    //   PEN_START/PEN_STOP per stroke         -> panel frozen
    //   ...plus resetViewUpdateMode(view)     -> black screen
    //
    // Both owners of that lifecycle -- EAC in "eac" mode, SFTouchRender in
    // "sdk" mode -- drive it correctly from inside. Two owners of the panel's
    // refresh state is exactly how it ends up stuck in one.

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

# 4b. Re-attribute the pen to the LorieView.
#
# termux-x11 puts its touch listener on the PARENT, so the framework's
# dispatchTouchEvent hook fires with the FrameLayout and EAC -- whose config
# names the LorieView -- calls every sample "outside draw view" and PAUSES
# handwriting. Feeding the same event back against the LorieView is what makes
# EAC's own acceleration apply. Runbook 13 4p has the log that proves it.
edit("lorie/src/main/java/com/termux/x11/MainActivity.java",
     """            if (e.getAction() == MotionEvent.ACTION_DOWN)
                lorieParent.requestUnbufferedDispatch(e);""",
     """            OnyxPen.onMotion(e);

            if (e.getAction() == MotionEvent.ACTION_DOWN)
                lorieParent.requestUnbufferedDispatch(e);""",
     "OnyxPen.onMotion() in the touch listener")

edit("lorie/src/main/java/com/termux/x11/MainActivity.java",
     """        lorieParent.setOnHoverListener((v, e) -> mInputHandler.handleTouchEvent(lorieParent, lorieView, e));
        lorieParent.setOnGenericMotionListener((v, e) -> mInputHandler.handleTouchEvent(lorieParent, lorieView, e));""",
     """        lorieParent.setOnHoverListener((v, e) -> mInputHandler.handleTouchEvent(lorieParent, lorieView, e));
        lorieParent.setOnGenericMotionListener((v, e) -> mInputHandler.handleTouchEvent(lorieParent, lorieView, e));

        // Onyx: make the LorieView ITSELF the touch target.
        //
        // This is the whole fix, and it is not about our code seeing the event
        // -- it is about which view the FRAMEWORK sees. This firmware patches
        // android.view.View to consult EAC on two hooks, and both are passed
        // `this`:
        //
        //   dispatchTouchEvent -> EACScreenNoteManager.onDispatchMotionEvent(this, e)
        //   beforeScroll       -> EACScreenNoteManager.allowApplyTransientUpdate(this, e)
        //
        // The second one is the one that matters:
        //
        //   if (!isDrawingViewWithEnableConfig(v, noteConfig)) return true;
        //   return !isStylus(e);
        //
        // Matching draw view + stylus => FALSE => EAC suppresses its
        // refresh-on-touch. Not matching => TRUE => the screen refreshes on
        // pen-down, and the kernel treats that DRM commit as a reason to leave
        // handwriting scheme:
        //
        //   onyx_tcon_set_upd_scheme(): setting upd scheme level to 4. drm_change[1]
        //
        // Measured: handwriting scheme was held for 10.8 ms of a 505 ms stroke,
        // and SurfaceFlinger (which owns /dev/ebc) painted nothing --
        // 0 `ebc_ioctl(): HANDWRITE` against dozens for the native canvas.
        //
        // Because termux-x11 listens on the PARENT, the parent is the touch
        // target and both hooks fire with the FrameLayout. Attaching here
        // makes the LorieView consume the event instead, so the framework
        // passes the view our EAC config actually names. Nothing else changes:
        // the same handler runs, with view0 == view, so it applies no
        // coordinate offset -- which is right, the event is already in this
        // view's space.
        if (OnyxPen.wantsViewTouchTarget(lorieView.getContext())) {
            lorieView.setOnTouchListener((v, e) -> {
                OnyxPen.onStrokeStart(e);

                if (e.getAction() == MotionEvent.ACTION_DOWN)
                    lorieView.requestUnbufferedDispatch(e);

                return mInputHandler.handleTouchEvent(lorieView, lorieView, e);
            });
            lorieView.setOnHoverListener((v, e) -> mInputHandler.handleTouchEvent(lorieView, lorieView, e));
            lorieView.setOnGenericMotionListener((v, e) -> mInputHandler.handleTouchEvent(lorieView, lorieView, e));
        }""",
     "LorieView as the touch target")

print("onyx-pen patch applied")
