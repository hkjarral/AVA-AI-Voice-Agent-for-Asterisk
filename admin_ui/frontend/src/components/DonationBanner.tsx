import { Coffee, Heart, X } from 'lucide-react';
import { KOFI_URL, SPONSORS_URL } from '../config/donation';

interface DonationBannerProps {
  callCount?: number;
  onLater: () => void;
  onDismiss: () => void;
  onDonate: () => void;
}

export default function DonationBanner({
  callCount,
  onLater,
  onDismiss,
  onDonate,
}: DonationBannerProps) {
  const lead =
    callCount !== undefined
      ? `AVA has handled ${callCount.toLocaleString()} calls for you.`
      : 'Thanks for running AVA.';

  return (
    <div
      data-testid="donation-banner"
      className="mb-4 flex flex-col gap-3 rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground sm:flex-row sm:items-center sm:justify-between"
    >
      <p>
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
          className="inline-flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-primary-foreground"
        >
          <Coffee className="h-4 w-4" /> Support on Ko-fi
        </a>
        <a
          href={SPONSORS_URL}
          target="_blank"
          rel="noopener noreferrer"
          onClick={onDonate}
          aria-label="Sponsor AVA on GitHub"
          className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5"
        >
          <Heart className="h-4 w-4" /> Sponsor
        </a>
        <button
          type="button"
          onClick={onLater}
          className="rounded-md px-3 py-1.5 hover:text-foreground"
        >
          Maybe later
        </button>
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Don't show again"
          className="inline-flex items-center gap-1 rounded-md px-2 py-1.5 hover:text-foreground"
        >
          <X className="h-3.5 w-3.5" /> Don&apos;t show again
        </button>
      </div>
    </div>
  );
}
