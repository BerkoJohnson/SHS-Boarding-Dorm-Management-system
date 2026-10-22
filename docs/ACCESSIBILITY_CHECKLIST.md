# Accessibility and mobile operability checklist

This checklist covers the F-34 remediation. Automated source checks are done;
the physical Android test and screen-reader test require target access.

## Implemented in the application

- A visible-on-focus **Skip to main content** link on every page.
- A global `:focus-visible` outline for keyboard users.
- Base operational text raised to 14px, with 16px form inputs on phones.
- Gate departure and return buttons are at least 44px high and use 14px labels.
- Gate verification labels are larger and higher contrast.
- Existing mobile sidebar links and form controls use a 44px minimum height.
- Status text is not conveyed by colour alone; the pass status is written out.

## Manual Android test (pending)

Use a representative school Android phone or tablet in bright indoor light and
outdoors. Sign in to staging with a fictional gate account.

1. Open **Gate → Verify pass** and search for a pass.
2. Measure the departure and return button heights; confirm at least 44×44px.
3. Tap each button with a thumb while holding the device one-handed. Confirm no
   accidental double submission.
4. Confirm the pass status, student name, destination, and times are readable
   without zooming.
5. Rotate the device and repeat. Confirm no content is clipped.
6. Turn on Android TalkBack and confirm the student name, pass status, and
   action buttons are announced in a useful order.
7. Repeat on a roll-call page and a report export page.
8. Record the device model, Android version, browser, and any failures below.

| Date | Device | Android/browser | Gate | Roll call | Reports | Notes |
|---|---|---|---|---|---|---|
|  |  |  |  |  |  |  |

## Keyboard-only test (staging)

1. Reload the dashboard and press Tab once. Confirm **Skip to main content**
   appears and moves focus to the main region.
2. Tab through the sidebar and gate actions; confirm focus is always visible.
3. Complete a gate departure and return without a mouse.
4. Open and close the mobile navigation with the keyboard.
5. Confirm no focus trap except while a modal is deliberately open.

## Automated checks

Run before release:

```powershell
python manage.py check --settings=schoolsystem.settings.test
python manage.py test core.test_full_p1 --settings=schoolsystem.settings.test --keepdb
```

If an axe or Lighthouse scan is available, scan the gate, roll-call, exeat, and
report pages and record critical issues. Do not close an accessibility finding
without a retest on the same device/browser.
