import './globals.css';
import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'WinScope — Football Predictions',
  description: 'AI-assisted predictions for Betway Soccer Tote pools.',
  icons: {
    icon: [{ url: '/favicon.png', type: 'image/png' }],
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
