const { withPodfile, withXcodeProject } = require("expo/config-plugins");

const marker = "# ReelBot: Xcode 26 fmt compatibility";

/**
 * React Native 0.81 bundles fmt 11, which fails to compile as C++20 with the
 * stricter Apple Clang shipped in Xcode 26.4+. Compile only fmt as C++17;
 * the rest of React Native continues to use its configured C++ standard.
 */
module.exports = function withXcode26FmtFix(config) {
  config = withPodfile(config, (podfileConfig) => {
    const contents = podfileConfig.modResults.contents;
    if (contents.includes(marker)) {
      return podfileConfig;
    }

    const reactNativePostInstall = /(    react_native_post_install\([\s\S]*?^    \)\n)/m;
    if (!reactNativePostInstall.test(contents)) {
      throw new Error("Could not locate react_native_post_install in the iOS Podfile");
    }

    const workaround = `${marker}
    installer.pods_project.targets.each do |target|
      next unless target.name == 'fmt'

      target.build_configurations.each do |build_config|
        build_config.build_settings['CLANG_CXX_LANGUAGE_STANDARD'] = 'c++17'
      end
    end
`;

    podfileConfig.modResults.contents = contents.replace(
      reactNativePostInstall,
      `$1${workaround}`
    );
    return podfileConfig;
  });

  return withXcodeProject(config, (xcodeConfig) => {
    const teamId = xcodeConfig.ios?.appleTeamId;
    const buildNumber = xcodeConfig.ios?.buildNumber;
    if (!teamId && !buildNumber) {
      return xcodeConfig;
    }

    const project = xcodeConfig.modResults;
    const nativeTargets = project.pbxNativeTargetSection();
    const configurationLists = project.pbxXCConfigurationList();
    const buildConfigurations = project.pbxXCBuildConfigurationSection();

    for (const [targetId, target] of Object.entries(nativeTargets)) {
      if (targetId.endsWith("_comment") || !target) {
        continue;
      }

      const configurationList = configurationLists[target.buildConfigurationList];
      for (const configuration of configurationList?.buildConfigurations || []) {
        const buildConfiguration = buildConfigurations[configuration.value];
        if (buildConfiguration?.buildSettings) {
          if (buildNumber) {
            buildConfiguration.buildSettings.CURRENT_PROJECT_VERSION = buildNumber;
          }
          if (
            teamId &&
            target.productType === '"com.apple.product-type.app-extension"'
          ) {
            buildConfiguration.buildSettings.DEVELOPMENT_TEAM = teamId;
            buildConfiguration.buildSettings.CODE_SIGN_STYLE = "Automatic";
          }
        }
      }
    }

    return xcodeConfig;
  });
};
