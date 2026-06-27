// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import DonationBanner from './DonationBanner';
import { KOFI_URL, SPONSORS_URL } from '../config/donation';

const handlers = () => ({ onLater: vi.fn(), onDismiss: vi.fn(), onDonate: vi.fn() });

describe('DonationBanner', () => {
  it('renders the call count when provided', () => {
    render(<DonationBanner callCount={1234} {...handlers()} />);
    expect(screen.getByText(/1,234 calls/)).toBeInTheDocument();
  });

  it('uses generic copy when count is absent', () => {
    render(<DonationBanner {...handlers()} />);
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

  it('fires the callbacks', async () => {
    const h = handlers();
    render(<DonationBanner callCount={10} {...h} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole('button', { name: 'Maybe later' }));
    expect(h.onLater).toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: "Don't show again" }));
    expect(h.onDismiss).toHaveBeenCalled();
    await user.click(screen.getByRole('link', { name: 'Support AVA on Ko-fi' }));
    expect(h.onDonate).toHaveBeenCalled();
  });
});
