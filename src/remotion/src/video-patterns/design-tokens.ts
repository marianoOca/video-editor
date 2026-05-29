// Design tokens - reusable color, font, spacing, and radius system
// Combines patterns from agendia (medical premium) and caro (personal/family) themes

export const createDesignTokens = (options?: {
  // Color palette overrides
  colors?: Partial<{
    background: string;
    card: string;
    foreground: string;
    primary: string;
    primaryLight: string;
    primaryDark: string;
    accent: string;
    accentLight: string;
    accentDark: string;
    soft: string;
    muted: string;
    border: string;
    destructive: string;
    destructiveSoft: string;
    whatsappBubbleIn: string;
    whatsappBubbleOut: string;
    white: string;
  }>;

  // Font family overrides
  fonts?: Partial<{
    serif: string;
    sans: string;
    chat: string;
  }>;

  // Font size overrides (px)
  sizes?: Partial<{
    displayHuge: number;
    displayLarge: number;
    displayMedium: number;
    displaySmall: number;
    headlineLarge: number;
    headlineMedium: number;
    body: number;
    bodySmall: number;
    caps: number;
    subhead: number;
    mediumText: number;
    mediumSmallText: number;
    smallText: number;
  }>;

  // Spacing overrides (px)
  spacing?: Partial<{
    xs: number;
    sm: number;
    md: number;
    lg: number;
    xl: number;
    xxl: number;
  }>;

  // Border radius overrides (px)
  radii?: Partial<{
    sm: number;
    md: number;
    lg: number;
    xl: number;
    pill: number;
  }>;

  // Animation timing overrides (frames at 30fps)
  timing?: Partial<{
    short: number;
    base: number;
    long: number;
    veryLong: number;
  }>;
}) => {
  // Base design tokens - neutral/default values
  const baseColors = {
    background: "#FAF5ED", // warm cream/ivory
    card: "#F2EBE0",
    foreground: "#1F3329", // dark green-black
    primary: "#445E51", // sage green
    primaryLight: "#7A9A86",
    primaryDark: "#2E4339",
    accent: "#D4A03A", // gold (FA brand)
    accentLight: "#E8BD5F",
    accentDark: "#A87C26",
    soft: "#EBD9D3", // dusty rose
    muted: "#9B9282",
    border: "#DCD2C3",
    destructive: "#D63D3D",
    destructiveSoft: "#FCE5E5",
    whatsappBubbleIn: "#F4EFE7", // received bubble (patient)
    whatsappBubbleOut: "#DCE7DF", // sent bubble (clinic) soft sage
    white: "#FFFFFF",
  };

  const baseFonts = {
    serif: '"Cormorant Garamond", "Playfair Display", Georgia, serif',
    sans: '"Outfit", system-ui, -apple-system, BlinkMacSystemFont, sans-serif',
    chat: '"-apple-system", BlinkMacSystemFont, "Segoe UI", "Helvetica Neue", Arial, sans-serif',
  };

  const baseSizes = {
    // Display (primary emotional phrases)
    displayHuge: 160,
    displayLarge: 130,
    displayMedium: 110,
    displaySmall: 80,
    // Secondary headlines
    headlineLarge: 64,
    headlineMedium: 52,
    // Body
    body: 44,
    bodySmall: 36,
    // Shared inline sizes (caps labels + sans subheads across scenes)
    caps: 40,
    subhead: 38,
    mediumText: 30,
    mediumSmallText: 27,
    smallText: 23,
  };

  const baseSpacing = {
    xs: 8,
    sm: 16,
    md: 24,
    lg: 40,
    xl: 64,
    xxl: 96,
  };

  const baseRadii = {
    sm: 8,
    md: 16,
    lg: 28,
    xl: 40,
    pill: 9999,
  };

  const baseTiming = {
    short: 8,
    base: 15,
    long: 24,
    veryLong: 36,
  };

  return {
    colors: { ...baseColors, ...options?.colors },
    fonts: { ...baseFonts, ...options?.fonts },
    sizes: { ...baseSizes, ...options?.sizes },
    spacing: { ...baseSpacing, ...options?.spacing },
    radii: { ...baseRadii, ...options?.radii },
    timing: { ...baseTiming, ...options?.timing },
  };
};

// Predefined themes for common use cases
export const themes = {
  // Medical/premium theme (based on agendia)
  medical: createDesignTokens({
    colors: {
      background: "#FAF5ED",
      card: "#F2EBE0",
      foreground: "#1F3329",
      primary: "#445E51",
      primaryLight: "#7A9A86",
      primaryDark: "#2E4339",
      accent: "#D4A03A",
      accentLight: "#E8BD5F",
      accentDark: "#A87C26",
      soft: "#EBD9D3",
      muted: "#9B9282",
      border: "#DCD2C3",
      destructive: "#D63D3D",
      destructiveSoft: "#FCE5E5",
      whatsappBubbleIn: "#F4EFE7",
      whatsappBubbleOut: "#DCE7DF",
      white: "#FFFFFF",
    },
    fonts: {
      serif: '"Cormorant Garamond", "Playfair Display", Georgia, serif',
      sans: '"Outfit", system-ui, -apple-system, BlinkMacSystemFont, sans-serif',
      chat: '"-apple-system", BlinkMacSystemFont, "Segoe UI", "Helvetica Neue", Arial, sans-serif',
    },
    sizes: {
      displayHuge: 160,
      displayLarge: 130,
      body: 44,
      bodySmall: 36,
      caps: 40,
      subhead: 38,
      mediumText: 30,
      mediumSmallText: 27,
      smallText: 23,
    },
  }),

  // Personal/family theme (based on caro)
  personal: createDesignTokens({
    colors: {
      background: "#000000", // Black background for caro
      card: "#1A1A1A",
      foreground: "#F8F4E3", // Cream/off-white for contrast
      primary: "#4A5568", // Muted blue-gray
      primaryLight: "#6B7280",
      primaryDark: "#374151",
      accent: "#FBBF24", // Warm amber/gold
      accentLight: "#FCD34D",
      accentDark: "#F59E0B",
      soft: "#EED3C7",
      muted: "#A8A29E",
      border: "#374151",
      destructive: "#DC2626",
      destructiveSoft: "#FEF2F2",
      whatsappBubbleIn: "#374151",
      whatsappBubbleOut: "#4B5563",
      white: "#FFFFFF",
    },
    fonts: {
      serif: '"Great Vibes", "Cormorant Garamond", cursive',
      sans: '"Cormorant Garamond", "Helvetica Neue", serif',
      chat: '"Helvetica Neue", Arial, sans-serif',
    },
    sizes: {
      displayHuge: 100,
      displayLarge: 80,
      body: 32,
      bodySmall: 24,
      caps: 28,
      subhead: 26,
      mediumText: 22,
      mediumSmallText: 20,
      smallText: 18,
    },
  }),

  // Clean/modern theme
  modern: createDesignTokens({
    colors: {
      background: "#FFFFFF",
      card: "#F8F9FA",
      foreground: "#212529",
      primary: "#0D6EFD",
      primaryLight: "#6EA8FE",
      primaryDark: "#0A58CA",
      accent: "#198754",
      accentLight: "#6FDD8C",
      accentDark: "#157347",
      soft: "#E2E8F0",
      muted: "#6C757D",
      border: "#DEE2E6",
      destructive: "#DC3545",
      destructiveSoft: "#F8D7DA",
      whatsappBubbleIn: "#E9ECEF",
      whatsappBubbleOut: "#D1E7DD",
      white: "#FFFFFF",
    },
    fonts: {
      serif: '"Georgia", "Times New Roman", serif',
      sans: '"Inter", "Helvetica Neue", Arial, sans-serif',
      chat: '"Helvetica Neue", Arial, sans-serif',
    },
    sizes: {
      displayHuge: 96,
      displayLarge: 72,
      body: 28,
      bodySmall: 22,
      caps: 24,
      subhead: 22,
      mediumText: 18,
      mediumSmallText: 16,
      smallText: 14,
    },
  }),
};

// Hook for using design tokens in React components
export const useDesignTokens = (themeName: keyof typeof themes | null = null) => {
  // In a real implementation, this would use React Context
  // For now, we return the theme or base tokens
  if (themeName && themes[themeName as keyof typeof themes]) {
    return themes[themeName as keyof typeof themes];
  }
  return createDesignTokens();
};