# Finding the `com.postnord.app://` redirect URL

When you set up a PostNord **account**, the integration shows you a login link.
After you log in, PostNord finishes by sending your browser to an address that
starts with `com.postnord.app://redirect`. That address belongs to the PostNord
app, and no desktop browser can open it, so **the login visibly stops at the
last step**: you get a blank page, an error, or nothing happens. That is
expected, not a bug. Home Assistant needs that address, so you catch it in your
browser's developer tools before it disappears.

You only do this once per login. After that the integration renews its own
access, and it only asks again if PostNord stops accepting it.

The steps are the same in every browser:

1. Open an **empty tab**, then open the developer tools and switch to the
   **Network** tab *before* you open the login link.
2. Turn on **"Preserve log"** (the wording differs per browser; see below), so
   the list isn't cleared when the login redirects.
3. Open the login link from the Home Assistant form in that tab and log in to
   PostNord as usual: your email and the code PostNord emails you, or BankID /
   MitID if that is how you log in. If you are already logged in to PostNord,
   the login finishes immediately, which is expected.
4. Type `code=` into the Network tab's **filter** box. Copy the address that
   starts with `com.postnord.app://redirect?code=`.
5. **Not listed?** Clear the filter, click the **last** request to
   `account.postnord.com`, and look under its **Response Headers** for
   **`location`**. Its value is the address you need.
6. Paste the whole address into the Home Assistant form **within a few
   minutes**. The code in it expires quickly and works only once.

The sections below show where to click in each browser.

## Google Chrome / Microsoft Edge

1. Press `F12` (Windows/Linux) or `Cmd+Option+I` (Mac), or right-click the
   page → **Inspect**.
2. Click the **Network** tab.
3. Tick **"Preserve log"** near the top of the Network panel.
4. Open the login link in this tab and log in.
5. Type `code=` into the **Filter** box. Click the matching request, and under
   **Headers → General → Request URL** copy the full address.
6. If nothing matches, clear the filter and click the last
   `account.postnord.com` request. Under **Headers → Response Headers** copy
   the value of **location**.

## Mozilla Firefox

1. Press `F12` or right-click → **Inspect**.
2. Click the **Network** tab.
3. Click the **gear icon** (⚙) in the Network panel and enable **"Persist
   Logs"**, which is Firefox's name for "preserve log".
4. Open the login link in this tab and log in.
5. Type `code=` into the filter box, click the request and copy its **URL**
   from the **Headers** tab.
6. If nothing matches, click the last `account.postnord.com` request and copy
   **location** from its **Response Headers**.

## Safari

1. Open **Safari → Settings → Advanced** and turn on **"Show features for web
   developers"**.
2. **Develop → Show Web Inspector** (`Cmd+Option+I`), then the **Network** tab.
3. Enable **"Preserve Log"** in the Network tab's toolbar.
4. Open the login link in this tab and log in.
5. Search for `code=` in the filter field. If nothing matches, select the last
   `account.postnord.com` request and copy **location** from its response
   headers.

## Mobile browsers

Phone browsers have no developer tools with a Network tab. Do the login on a
desktop or laptop browser instead. The address isn't tied to a device, so it
doesn't matter where you catch it, as long as you paste it into your Home
Assistant.

## Troubleshooting

- **No request contains `code=`, and there is no `location` header.** Turn on
  "Preserve log" / "Persist Logs" *before* opening the login link. Without it,
  the redirect clears the list before you can look.
- **Home Assistant says it isn't the redirect URL of this login.** Paste the
  address exactly as shown, including `?code=…&state=…`. Use the link from the
  form you are pasting into: a link from an earlier attempt belongs to a
  different login. If a few minutes have passed, open the same link again, log
  in, and copy the new address. The old code only works once.
- **The login never gets past PostNord's own page.** Nothing can be caught
  until the login itself succeeds, so finish the email code or BankID / MitID
  step first.
