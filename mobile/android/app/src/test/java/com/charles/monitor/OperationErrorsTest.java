package com.charles.monitor;

import org.junit.Test;
import static org.junit.Assert.*;

public class OperationErrorsTest {
    @Test public void keepsActionableLoginAndExpiryMessages() {
        for (String detail : new String[]{"请先完成 Tailscale 登录，等待网络状态 Running。", "配对二维码已过期，请在电脑重新生成后再扫描"})
            assertEquals(detail,OperationErrors.message(new IllegalStateException(detail)));
    }
    @Test public void doesNotExposeCredentials() {
        assertFalse(OperationErrors.message(new Exception("token=secret https://private.ts.net/secret")).contains("secret"));
    }
    @Test public void handlesErrorsWithoutMessages() {
        assertTrue(OperationErrors.message(new Exception()).contains("操作未完成"));
        assertTrue(OperationErrors.message(new NoClassDefFoundError("private-internals")).contains("组件加载失败"));
    }
}
