import { useState } from 'react';
import { Coffee, Heart, X } from 'lucide-react';
import { KOFI_URL, SPONSORS_URL } from '../config/donation';

interface DonationBannerProps {
  callCount?: number;
  onLater: () => void;
  onDismiss: () => void;
  onDonate: () => void;
  onAlreadyDonated: () => void;
  onKeepReminders: () => void;
}

const FOCUS =
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-ring';
const PRIMARY_BTN = `inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-primary-foreground ${FOCUS}`;
const OUTLINE_BTN = `inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-foreground hover:bg-accent transition-colors ${FOCUS}`;
const SECONDARY_BTN = `inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-muted-foreground hover:bg-accent hover:text-foreground transition-colors ${FOCUS}`;
const CONTAINER =
  'mb-4 flex flex-col gap-3 rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground sm:flex-row sm:items-center sm:justify-between';

export default function DonationBanner({
  callCount,
  onLater,
  onDismiss,
  onDonate,
  onAlreadyDonated,
  onKeepReminders,
}: DonationBannerProps) {
  const [confirming, setConfirming] = useState(false);

  if (confirming) {
    return (
      <div data-testid="donation-banner" className={CONTAINER}>
        <p>
          <span aria-hidden="true">🙁</span>{' '}
          <span className="font-medium text-foreground">Really?</span> AVA&apos;s
          development runs on these contributions.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            className={SECONDARY_BTN}
            onClick={() => {
              setConfirming(false);
              onKeepReminders();
            }}
          >
            Keep reminders
          </button>
          <button type="button" className={SECONDARY_BTN} onClick={onDismiss}>
            Yes, hide for good
          </button>
        </div>
      </div>
    );
  }

  const lead =
    callCount !== undefined
      ? `AVA has handled ${callCount.toLocaleString()} calls for you.`
      : 'Thanks for running AVA.';

  return (
    <div data-testid="donation-banner" className={CONTAINER}>
      <p>
        <span className="font-medium text-foreground">{lead}</span>{' '}
        It&apos;s free and self-hosted — if it&apos;s saving you time, supporting
        development helps keep it going.
      </p>
      <div className="flex flex-col gap-2 sm:items-end">
        <div className="flex flex-wrap items-center gap-2">
          <a
            href={KOFI_URL}
            target="_blank"
            rel="noopener noreferrer"
            onClick={onDonate}
            aria-label="Support AVA on Ko-fi"
            className={PRIMARY_BTN}
          >
            <Coffee className="w-4 h-4" aria-hidden="true" /> Support on Ko-fi
          </a>
          <a
            href={SPONSORS_URL}
            target="_blank"
            rel="noopener noreferrer"
            onClick={onDonate}
            aria-label="Sponsor AVA on GitHub"
            className={OUTLINE_BTN}
          >
            <Heart className="w-4 h-4" aria-hidden="true" /> Sponsor
          </a>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className={SECONDARY_BTN} onClick={onAlreadyDonated}>
            I already donated
          </button>
          <button type="button" className={SECONDARY_BTN} onClick={onLater}>
            Maybe later
          </button>
          <button
            type="button"
            className={SECONDARY_BTN}
            onClick={() => setConfirming(true)}
            aria-label="Don't show again"
          >
            <X className="w-3.5 h-3.5" aria-hidden="true" /> Don&apos;t show again
          </button>
        </div>
      </div>
    </div>
  );
}
