# Active Firefox WebExtension capture

## What changed

The project moved away from Selenium/Marionette and temporary Firefox profiles toward a Firefox WebExtension that operates on the already-running Firefox instance.

The extension captures the currently selected tab's live DOM and sends the result to the local application.

## Why

The original Selenium approach created a separate browser context and therefore did not naturally share the user's already-authenticated Firefox session.

The desired behavior was:

- use the Firefox instance the user already has open;
- keep existing logins and sessions;
- let the user choose the tab they want;
- avoid restarting Firefox with special automation settings;
- leave Firefox open normally.

## Result

The capture happens from the DOM already present in the user's Firefox tab rather than loading the page again in a second browser profile.
