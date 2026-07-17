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
  it('renders completed-call impact and concrete funding copy', () => {
    render(<DonationBanner callCount={1234} {...handlers()} />);
    expect(screen.getByText(/1,234 calls/)).toBeInTheDocument();
    expect(screen.getByText(/PBX and provider compatibility testing/)).toBeInTheDocument();
  });

  it('Ko-fi link has correct href, target and rel', () => {
    render(<DonationBanner callCount={10} {...handlers()} />);
    const link = screen.getByRole('link', { name: 'Contribute $5 to AVA on Ko-fi' });
    expect(link).toHaveAttribute('href', KOFI_URL);
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('Sponsor link points at GitHub Sponsors', () => {
    render(<DonationBanner callCount={10} {...handlers()} />);
    expect(screen.getByRole('link', { name: 'Sponsor AVA as a business on GitHub' })).toHaveAttribute(
      'href',
      SPONSORS_URL,
    );
  });

  it('both donate links call onDonate', async () => {
    const h = handlers();
    render(<DonationBanner callCount={10} {...h} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole('link', { name: 'Contribute $5 to AVA on Ko-fi' }));
    await user.click(screen.getByRole('link', { name: 'Sponsor AVA as a business on GitHub' }));
    expect(h.onDonate).toHaveBeenCalledTimes(2);
  });

  it('fires onAlreadyDonated and onLater', async () => {
    const h = handlers();
    render(<DonationBanner callCount={10} {...h} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'I already support AVA' }));
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
    expect(screen.getByText(/Hide donation reminders\?/)).toBeInTheDocument();
  });

  it('confirm Hide reminders calls onDismiss', async () => {
    const h = handlers();
    render(<DonationBanner callCount={10} {...h} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: "Don't show again" }));
    await user.click(screen.getByRole('button', { name: 'Hide reminders' }));
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
