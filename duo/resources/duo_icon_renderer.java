import android.content.Context;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.drawable.AdaptiveIconDrawable;
import android.graphics.drawable.Drawable;
import android.os.Looper;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.lang.reflect.Method;

/**
 * On-device icon renderer for Duo (duo/core/apps.py pushes this dex).
 *
 * Usage: CLASSPATH=/data/local/tmp/duo_icons.dex app_process / DuoIconRenderer <pkg-list> <outdir>
 * The package list file holds one package name per line; for each package
 * writes <outdir>/<package>.png plus a tab-separated <outdir>/labels.txt
 * with package, kind (adaptive|legacy), versionCode and label. Adaptive
 * icons render as the full 108-unit artwork on a 432px canvas (the PC
 * side crops the 72-unit visible centre); legacy drawables render at
 * intrinsic size.
 */
public class DuoIconRenderer {

    private static final int ADAPTIVE_CANVAS = 432;
    private static final int LEGACY_CAP = 576;

    public static void main(String[] args) {
        int status = 1;
        try {
            BufferedReader in = new BufferedReader(
                    new InputStreamReader(new FileInputStream(args[0]), "UTF-8"));
            File outDir = new File(args[1]);
            outDir.mkdirs();
            Context context = systemContext();
            PackageManager pm = context.getPackageManager();
            int densityDpi = context.getResources().getDisplayMetrics().densityDpi;
            Writer meta = new OutputStreamWriter(
                    new FileOutputStream(new File(outDir, "labels.txt")), "UTF-8");
            String line;
            while ((line = in.readLine()) != null) {
                String pkg = line.trim();
                if (pkg.isEmpty()) {
                    continue;
                }
                try {
                    ApplicationInfo info = pm.getApplicationInfo(pkg, 0);
                    // Not getApplicationIcon(pkg): on some ROMs (ColorOS) it
                    // resolves every package to the default adaptive icon or
                    // throws SecurityException. Manual resource loading works,
                    // pinned to the display density so legacy-only icons come
                    // back at their sharpest variant, not the default bucket.
                    Drawable icon = pm.getResourcesForApplication(pkg)
                            .getDrawableForDensity(
                                    info.icon, densityDpi, context.getTheme());
                    Bitmap bitmap = render(icon);
                    File png = new File(outDir, pkg + ".png");
                    FileOutputStream out = new FileOutputStream(png);
                    bitmap.compress(Bitmap.CompressFormat.PNG, 100, out);
                    out.close();
                    if (icon instanceof AdaptiveIconDrawable) {
                        AdaptiveIconDrawable aid = (AdaptiveIconDrawable) icon;
                        // Layer renders: the PC side needs the fg alpha
                        // bbox (artwork silhouette) to normalise content
                        // scale, which the flattened composite loses.
                        if (aid.getBackground() != null) {
                            writeLayer(outDir, pkg + ".bg.png", aid.getBackground());
                        }
                        if (aid.getForeground() != null) {
                            writeLayer(outDir, pkg + ".fg.png", aid.getForeground());
                        }
                    }
                    String kind = (icon instanceof AdaptiveIconDrawable) ? "adaptive" : "legacy";
                    String label = String.valueOf(pm.getApplicationLabel(info))
                            .replace('\t', ' ').replace('\n', ' ').replace('\r', ' ');
                    String versionName = "?";
                    long version = 0L;
                    try {
                        PackageInfo pi = pm.getPackageInfo(pkg, 0);
                        version = pi.getLongVersionCode();
                        if (pi.versionName != null) {
                            versionName = pi.versionName
                                    .replace('\t', ' ').replace('\n', ' ').replace('\r', ' ');
                        }
                    } catch (Exception ignored) {
                    }
                    meta.write(pkg + "\t" + kind + "\t" + version + "\t" + versionName + "\t" + label + "\n");
                } catch (Exception e) {
                    meta.write(pkg + "\terror\t0\t" + e.getClass().getSimpleName() + "\n");
                }
                meta.flush();
            }
            in.close();
            meta.close();
            status = 0;
        } catch (Exception e) {
            System.err.println("duo-icons: " + e);
        }
        System.exit(status);
    }

    private static int densityFor(Canvas canvas, Drawable icon) {
        if (icon instanceof android.graphics.drawable.BitmapDrawable) {
            android.graphics.Bitmap b = ((android.graphics.drawable.BitmapDrawable) icon).getBitmap();
            if (b != null && b.getDensity() > 0) {
                return b.getDensity();
            }
        }
        return canvas.getDensity() > 0 ? canvas.getDensity() : android.util.DisplayMetrics.DENSITY_DEFAULT;
    }

    private static Context systemContext() throws Exception {
        if (Looper.myLooper() == null) {
            Looper.prepareMainLooper();
        }
        Class<?> threadClass = Class.forName("android.app.ActivityThread");
        Object thread = threadClass.getMethod("systemMain").invoke(null);
        return (Context) threadClass.getMethod("getSystemContext").invoke(thread);
    }

    private static void writeLayer(File outDir, String name, Drawable layer) throws Exception {
        Bitmap bitmap = Bitmap.createBitmap(
                ADAPTIVE_CANVAS, ADAPTIVE_CANVAS, Bitmap.Config.ARGB_8888);
        layer.setBounds(0, 0, ADAPTIVE_CANVAS, ADAPTIVE_CANVAS);
        layer.draw(new Canvas(bitmap));
        FileOutputStream out = new FileOutputStream(new File(outDir, name));
        bitmap.compress(Bitmap.CompressFormat.PNG, 100, out);
        out.close();
    }

    private static Bitmap render(Drawable icon) {
        if (icon instanceof AdaptiveIconDrawable) {
            Bitmap bitmap = Bitmap.createBitmap(
                    ADAPTIVE_CANVAS, ADAPTIVE_CANVAS, Bitmap.Config.ARGB_8888);
            icon.setBounds(0, 0, ADAPTIVE_CANVAS, ADAPTIVE_CANVAS);
            icon.draw(new Canvas(bitmap));
            return bitmap;
        }
        int width = icon.getIntrinsicWidth();
        int height = icon.getIntrinsicHeight();
        if (width <= 0 || height <= 0) {
            width = height = ADAPTIVE_CANVAS;
        }
        int capped = Math.min(Math.max(width, height), LEGACY_CAP);
        Bitmap bitmap = Bitmap.createBitmap(capped, capped, Bitmap.Config.ARGB_8888);
        Canvas canvas = new Canvas(bitmap);
        canvas.setDensity(densityFor(canvas, icon));
        float box = Math.min((float) capped / width, (float) capped / height);
        int w = Math.round(width * box);
        int h = Math.round(height * box);
        icon.setBounds((capped - w) / 2, (capped - h) / 2, (capped + w) / 2, (capped + h) / 2);
        icon.draw(canvas);
        return bitmap;
    }
}
