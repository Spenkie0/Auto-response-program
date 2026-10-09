# Firefox native-messaging trigger

## What changed

The localhost HTTP bridge was replaced by Firefox Native Messaging.

The extension can act as a trigger: the user clicks the extension action while viewing the desired tab, the extension captures that tab's existing DOM, and Firefox launches/communicates with the local native host.

The scraper therefore no longer needs a `127.0.0.1:8765` HTTP listener.

## Why

The user wanted the extension to feel like a direct trigger for the program and wanted to remove the scraper's exposed local HTTP port.

Native Messaging is designed for communication between WebExtensions and locally installed applications.

Removing the polling HTTP bridge also simplifies the capture architecture:

```text
Firefox extension
      ↓
Native Messaging
      ↓
Python native host
      ↓
local pipeline
```

## Result

The scraper trigger is integrated more directly with Firefox, and the previous scraper-specific localhost listener is gone.

Ollama may still expose its own local API endpoint because the Python pipeline uses Ollama locally; that is separate from the Firefox capture channel.
