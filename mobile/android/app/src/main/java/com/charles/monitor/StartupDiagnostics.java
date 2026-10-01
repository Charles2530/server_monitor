package com.charles.monitor;

import android.app.ActivityManager;
import android.app.ApplicationExitInfo;
import android.content.Context;
import android.content.SharedPreferences;
import android.os.Build;
import android.system.Os;
import android.system.OsConstants;
import android.util.Log;
import java.io.File;
import java.io.FileDescriptor;
import java.io.FileOutputStream;
import java.io.RandomAccessFile;
import java.nio.charset.StandardCharsets;
import java.util.Date;

/** Local-only startup evidence; never reads device tokens or cloud settings. */
final class StartupDiagnostics {
    private final Context context;
    private final SharedPreferences state;
    private final File nativeLog;
    private final String previous;
    private FileDescriptor savedStderr;

    StartupDiagnostics(Context context) {
        this.context=context;
        state=context.getSharedPreferences("startup_diagnostics",Context.MODE_PRIVATE);
        nativeLog=new File(context.getNoBackupFilesDir(),"native-startup.log");
        previous="上次阶段："+state.getString("stage","尚无记录")+"\n"
                +state.getString("failure","")+"\n"+readNative();
    }
    void installHandler() {
        Thread.UncaughtExceptionHandler original=Thread.getDefaultUncaughtExceptionHandler();
        Thread.setDefaultUncaughtExceptionHandler((thread,error) -> {
            try {failure(error);} catch(Exception ignored) { }
            if(original!=null)original.uncaughtException(thread,error);
            else {android.os.Process.killProcess(android.os.Process.myPid());System.exit(1);}
        });
    }
    void stage(String name) {
        state.edit().putString("stage",new Date()+" · "+name).commit();
    }
    void failure(Throwable error) {
        state.edit().putString("failure",DiagnosticText.clean(Log.getStackTraceString(error))).commit();
    }
    void beginNativeOutput() throws Exception {
        // Go writes fatal startup stacks to fd 2, including before JNI initialization.
        savedStderr=Os.dup(FileDescriptor.err);
        try(FileOutputStream output=new FileOutputStream(nativeLog,false)) {
            Os.dup2(output.getFD(),2);
        } catch(Exception error) {endNativeOutput();throw error;}
    }
    void endNativeOutput() {
        if(savedStderr==null)return;
        try {Os.dup2(savedStderr,2);} catch(Exception ignored) { }
        try {Os.close(savedStderr);} catch(Exception ignored) { }
        savedStderr=null;
    }
    private String readNative() {
        if(!nativeLog.isFile())return "";
        try(RandomAccessFile file=new RandomAccessFile(nativeLog,"r")) {
            long size=file.length(); byte[] bytes=new byte[(int)Math.min(size,65536)];
            file.seek(Math.max(0,size-bytes.length));file.readFully(bytes);
            return DiagnosticText.clean(new String(bytes,StandardCharsets.UTF_8));
        } catch(Exception error) {return "原生启动日志读取失败："+error.getClass().getSimpleName();}
    }
    String report() {
        StringBuilder result=new StringBuilder("Charles Monitor\n");
        try {result.append("App ").append(context.getPackageManager().getPackageInfo(context.getPackageName(),0).versionName).append('\n');}
        catch(Exception ignored) { }
        result.append(Build.MANUFACTURER).append(' ').append(Build.MODEL)
                .append("\nAndroid ").append(Build.VERSION.RELEASE).append(" / API ").append(Build.VERSION.SDK_INT)
                .append("\nBuild ").append(Build.DISPLAY).append('\n');
        try {result.append("Page size: ").append(Os.sysconf(OsConstants._SC_PAGESIZE)).append('\n');}
        catch(Exception ignored) { }
        result.append(previous).append("\n当前阶段：").append(state.getString("stage","尚未启动"))
                .append('\n').append(state.getString("failure","")).append('\n').append(readNative());
        if(Build.VERSION.SDK_INT>=30) {
            try {
                ActivityManager manager=(ActivityManager)context.getSystemService(Context.ACTIVITY_SERVICE);
                for(ApplicationExitInfo info:manager.getHistoricalProcessExitReasons(context.getPackageName(),0,3)) {
                    result.append("\n系统退出记录：").append(new Date(info.getTimestamp()))
                            .append(" reason=").append(info.getReason()).append(" status=").append(info.getStatus())
                            .append(" description=").append(info.getDescription());
                }
            } catch(Exception error) {result.append("\n系统退出记录不可读：").append(error.getClass().getSimpleName());}
        }
        return DiagnosticText.clean(result.toString());
    }
}
