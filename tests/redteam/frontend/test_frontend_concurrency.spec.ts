import { test, expect } from '@playwright/test';

test.describe('Phase 8.0: Red-Team Frontend Concurrency & State resilience', () => {

    test.beforeEach(async ({ page }) => {
        // Assuming backend is running locally on 8000 and frontend on 3000
        await page.goto('http://localhost:3000');
        // Wait for initial load
        await page.waitForSelector('textarea[placeholder="Type a message..."]');
    });

    test('Rapid Message Burst: Send 4 messages before stream finishes rendering', async ({ page }) => {
        const input = page.locator('textarea[placeholder="Type a message..."]');
        const sendButton = page.locator('button[type="submit"]');

        // Mocks: Assuming we intercept the network or rely on the real backend running
        // with a slow provider. Here we just blast the UI.

        // Send 4 rapid messages
        for (let i = 1; i <= 4; i++) {
            await input.fill(`Burst Message ${i}`);
            await sendButton.click();
            // Only wait a tiny amount to simulate aggressive user
            await page.waitForTimeout(50);
        }

        // Verify UI did not fracture
        // We should see all 4 user messages in the chat history
        const userMessages = page.locator('.flex.justify-end .bg-primary');
        await expect(userMessages).toHaveCount(4, { timeout: 10000 });

        // Verify text
        await expect(userMessages.nth(0)).toContainText('Burst Message 1');
        await expect(userMessages.nth(3)).toContainText('Burst Message 4');

        // Ensure the system didn't create duplicate phantom bots (it should handle the queue or reject)
        // Depending on backend implementation, we might get 1-4 responses. 
        // The key is that the React state didn't crash.
        const botMessages = page.locator('.flex.justify-start .bg-[hsl(var(--muted))]');
        // Wait for at least 1 response to start rendering
        await expect(botMessages.first()).toBeVisible({ timeout: 15000 });
    });

    test('WebSocket Reconnect Storm: 5x connect/disconnect cycles during delivery', async ({ page, context }) => {
        // Note: Playwright can simulate offline mode to test WebSocket drop

        const input = page.locator('textarea[placeholder="Type a message..."]');
        await input.fill('Write a long essay about the history of artificial intelligence.');
        await page.locator('button[type="submit"]').click();

        // Wait for the bot to start replying (meaning WS is active and streaming)
        const botMessages = page.locator('.flex.justify-start .bg-[hsl(var(--muted))]');
        await expect(botMessages).toBeVisible({ timeout: 10000 });

        // Rapidly toggle offline/online to break and reconnect WebSockets
        for (let i = 0; i < 5; i++) {
            await context.setOffline(true);
            await page.waitForTimeout(200);
            await context.setOffline(false);
            await page.waitForTimeout(500);
        }

        // System should either:
        // 1. Recover and continue streaming
        // 2. Gracefully show an error state without crashing React
        // 3. Allow the user to send a new message

        const chatInput = page.locator('textarea[placeholder="Type a message..."]');
        await expect(chatInput).toBeVisible();
        await expect(chatInput).toBeEnabled();

        // Verify we can still send a message after the storm
        await chatInput.fill('Are you still there?');
        await page.locator('button[type="submit"]').click();

        // Check if the new message appears
        const userMessages = page.locator('.flex.justify-end .bg-primary');
        await expect(userMessages.last()).toContainText('Are you still there?', { timeout: 5000 });
    });

    test('Barge-in Interrupts: Send new message during active stream', async ({ page }) => {
        const input = page.locator('textarea[placeholder="Type a message..."]');
        await input.fill('Count from 1 to 100 very slowly.');
        await page.locator('button[type="submit"]').click();

        // Wait for the stream to start
        const botMessages = page.locator('.flex.justify-start .bg-[hsl(var(--muted))]');
        await expect(botMessages).toBeVisible({ timeout: 10000 });

        // While it's streaming, barge in
        await page.waitForTimeout(1000); // let it stream a bit
        await input.fill('STOP! Start counting backwards from 10 instead.');
        await page.locator('button[type="submit"]').click();

        // The UI should now have 2 user messages and 2 bot messages (or 1 interrupted, 1 new)
        const userMessages = page.locator('.flex.justify-end .bg-primary');
        await expect(userMessages).toHaveCount(2);
        await expect(userMessages.nth(1)).toContainText('STOP!');

        // Wait for the second stream to begin
        await expect(botMessages).toHaveCount(2, { timeout: 10000 });
    });
});
