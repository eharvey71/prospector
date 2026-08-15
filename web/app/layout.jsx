import "./globals.css";

export const metadata = { title: "Job Engine" };

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
