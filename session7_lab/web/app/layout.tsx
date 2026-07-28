import "./globals.css";

export const metadata = {
  title: "ClaimAssist v2",
  description: "Insurance claims copilot — streaming chat via LiteLLM",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
