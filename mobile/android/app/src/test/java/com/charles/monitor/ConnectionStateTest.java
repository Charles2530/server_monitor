package com.charles.monitor;

import org.junit.Test;
import static org.junit.Assert.*;

public class ConnectionStateTest {
    @Test public void savedPairingIsNotReportedAsLostWhileNetworkRecovers() {
        String detail = ConnectionState.pairingDetail(true, null, false, "");
        assertTrue(detail.contains("已保存"));
        assertFalse(detail.contains("尚未配对"));
        assertEquals("尚未配对电脑", ConnectionState.pairingDetail(false, null, false, ""));
    }

    @Test public void loginDoesNotInterruptAnExistingOrRecoveringSession() {
        assertTrue(ConnectionState.shouldRequestLogin("NeedsLogin"));
        for (String state : new String[]{"Running", "Starting", "NoState", "NeedsMachineAuth", "Stopped"})
            assertFalse(state, ConnectionState.shouldRequestLogin(state));
    }

    @Test public void approvedAndPendingPermissionsRemainDistinct() {
        assertTrue(ConnectionState.pairingDetail(true, "approved", false, "test-device").contains("仅查看"));
        assertTrue(ConnectionState.pairingDetail(true, "approved", true, "test-device").contains("可管理任务"));
        assertTrue(ConnectionState.pairingDetail(true, "pending", false, "test-device").contains("等待电脑批准"));
    }
}
