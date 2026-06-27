// @vitest-environment jsdom
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import '@testing-library/jest-dom/vitest';
import DonationBanner from './DonationBanner';
import { KOFI_URL, SPONSORS_URL } from '../config/donation';

const handlers = () => ({
  onLater: vi.fn(),
  onDismiss: vi.fn(),
  onDonate: vi.fn(),
  onAlreadyDonated: vi.fn(),
  onKeepReminders: vi.fn(),
});

describe('DonationBanner', () => {
  it('renders the call count when provided', () => {
    render(<DonationBanner callCount={1234} {...handlers()} />);
    expect(screen.getByText(/1,234 calls/)).toBeInTheDocument();
  });

  it('uses generic copy when count is absent', () => {
    render(<DonationBanner {...handlers()} />);
    expect(screen.getByText(/Thanks for running AVA/)).toBeInTheDocument();
  });

  it('uses generic copy for a zero-call install', () => {
    render(<DonationBanner callCount={0} {...handlers()} />);
    expect(screen.getByText(/Thanks for running AVA/)).toBeInTheDocument();
  });

  it('Ko-fi link has correct href, target and rel', () => {
    render(<DonationBanner callCount={10} {...handlers()} />);
    const link = screen.getByRole('link', { name: 'Support AVA on Ko-fi' });
    expect(link).toHaveAttribute('href', KOFI_URL);
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('Sponsor link points at GitHub Sponsors', () => {
    render(<DonationBanner callCount={10} {...handlers()} />);
    expect(screen.getByRole('link', { name: 'Sponsor AVA on GitHub' })).toHaveAttribute(
      'href',
      SPONSORS_URL,
    );
  });

  it('both donate links call onDonate', async () => {
    const h = handlers();
    render(<DonationBanner callCount={10} {...h} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole('link', { name: 'Support AVA on Ko-fi' }));
    await user.click(screen.getByRole('link', { name: 'Sponsor AVA on GitHub' }));
    expect(h.onDonate).toHaveBeenCalledTimes(2);
  });

  it('fires onAlreadyDonated and onLater', async () => {
    const h = handlers();
    render(<DonationBanner callCount={10} {...h} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'I already donated' }));
    expect(h.onAlreadyDonated).toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Maybe later' }));
    expect(h.onLater).toHaveBeenCalled();
  });

  it("Don't show again opens a confirm instead of dismissing immediately", async () => {
    const h = handlers();
    render(<DonationBanner callCount={10} {...h} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: "Don't show again" }));
    expect(h.onDismiss).not.toHaveBeenCalled();
    expect(screen.getByText(/Really\?/)).toBeInTheDocument();
  });

  it('confirm Yes, hide for good calls onDismiss', async () => {
    const h = handlers();
    render(<DonationBanner callCount={10} {...h} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: "Don't show again" }));
    await user.click(screen.getByRole('button', { name: 'Yes, hide for good' }));
    expect(h.onDismiss).toHaveBeenCalled();
    expect(h.onKeepReminders).not.toHaveBeenCalled();
  });

  it('confirm Keep reminders calls onKeepReminders, not onDismiss', async () => {
    const h = handlers();
    render(<DonationBanner callCount={10} {...h} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: "Don't show again" }));
    await user.click(screen.getByRole('button', { name: 'Keep reminders' }));
    expect(h.onKeepReminders).toHaveBeenCalled();
    expect(h.onDismiss).not.toHaveBeenCalled();
  });
});
