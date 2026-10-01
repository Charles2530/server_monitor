package com.charles.monitor;

import org.junit.Test;
import static org.junit.Assert.*;

public class DiagnosticTextTest {
    @Test public void preservesUsefulCrashEvidence() {
        String crash="panic: test\ngoroutine 1:\ncharles.monitor/mobile.Start(client.go:52)\nUnsatisfiedLinkError";
        assertEquals(crash, DiagnosticText.clean(crash));
    }
    @Test public void removesCredentialsAndAddresses() {
        String output=DiagnosticText.clean("https://login.tailscale.com/a/secret token=abc password=def ticket=ghi Bearer secret tskey-auth-abcdef");
        assertFalse(output.contains("login.tailscale"));
        for(String secret:new String[]{"abc", "def", "ghi", "secret", "tskey-auth"})assertFalse(output.contains(secret));
    }
    @Test public void boundsReportAndKeepsBothEnds() {
        String output=DiagnosticText.clean("START"+new String(new char[50000]).replace('\0','x')+"END");
        assertTrue(output.length()<24100); assertTrue(output.startsWith("START")); assertTrue(output.endsWith("END"));
    }
}
