import { render, screen } from '@testing-library/react';
import PolicyScannerHeader from './PolicyScannerHeader';

describe('PolicyScannerHeader', () => {
  it('shows "Admin" when closed', () => {
    render(<PolicyScannerHeader onToggleAdmin={jest.fn()} adminOpen={false} />);
    expect(screen.getByRole('button', { name: 'Admin' })).toBeInTheDocument();
  });

  it('shows "Exit admin" when open', () => {
    render(<PolicyScannerHeader onToggleAdmin={jest.fn()} adminOpen />);
    expect(screen.getByRole('button', { name: 'Exit admin' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Close admin' })).not.toBeInTheDocument();
  });
});
