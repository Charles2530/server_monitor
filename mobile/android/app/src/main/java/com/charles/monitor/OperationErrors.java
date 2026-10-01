package com.charles.monitor;

/** Expected connection errors must remain actionable instead of showing only a Java class name. */
final class OperationErrors {
    static String message(Throwable error) {
        if (error instanceof LinkageError)
            return "应用组件加载失败，请复制诊断信息以便排查。";
        if (error.getClass().getName().equals("org.json.JSONException"))
            return "配对信息格式无效，请重新扫描电脑生成的配对二维码。";
        String detail = DiagnosticText.clean(error.getMessage());
        if (detail.trim().isEmpty())
            return "操作未完成，请重试或查看诊断信息（"+error.getClass().getSimpleName()+"）。";
        return detail.length() > 600 ? detail.substring(0,600)+"…" : detail;
    }
}
