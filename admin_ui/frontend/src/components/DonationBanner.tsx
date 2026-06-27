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
const BTN_BASE = `inline-flex h-8 items-center gap-1.5 rounded-md px-3 text-sm ${FOCUS}`;
const PRIMARY_BTN = `${BTN_BASE} bg-primary font-medium text-primary-foreground`;
const OUTLINE_BTN = `${BTN_BASE} border border-border font-medium text-foreground hover:bg-accent transition-colors`;
const SECONDARY_BTN = `${BTN_BASE} border border-border text-muted-foreground hover:bg-accent hover:text-foreground transition-colors`;
const CONTAINER =
  'mb-4 flex flex-col gap-3 rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground';
const ICON = 'w-4 h-4 shrink-0';

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
        <p className="leading-relaxed">
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
    callCount !== undefined && callCount > 0
      ? `AVA has handled ${callCount.toLocaleString()} calls for you.`
      : 'Thanks for running AVA.';

  return (
    <div data-testid="donation-banner" className={CONTAINER}>
      <p className="leading-relaxed">
        <span className="font-medium text-foreground">{lead}</span>{' '}
        It&apos;s free and self-hosted — if it&apos;s saving you time, supporting
        development helps keep it going.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <a
          href={KOFI_URL}
          target="_blank"
          rel="noopener noreferrer"
          onClick={onDonate}
          aria-label="Support AVA on Ko-fi"
          className={PRIMARY_BTN}
        >
          <Coffee className={ICON} aria-hidden="true" /> Support on Ko-fi
        </a>
        <a
          href={SPONSORS_URL}
          target="_blank"
          rel="noopener noreferrer"
          onClick={onDonate}
          aria-label="Sponsor AVA on GitHub"
          className={OUTLINE_BTN}
        >
          <Heart className={ICON} aria-hidden="true" /> Sponsor
        </a>
        <span
          aria-hidden="true"
          className="mx-1 hidden h-5 w-px shrink-0 bg-border sm:inline-block"
        />
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
          <X className="w-3.5 h-3.5 shrink-0" aria-hidden="true" /> Don&apos;t show again
        </button>
      </div>
    </div>
  );
}
