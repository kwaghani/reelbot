const { withFinalizedMod } = require('expo/config-plugins');
const fs = require('fs');
const path = require('path');
const plist = require('@expo/plist').default;
// Replace generated files atomically; truncating an offloaded Desktop file can stall.
function writeGenerated(file, contents) {
  const temporary = `${file}.reelbot-${process.pid}.tmp`;
  try { fs.writeFileSync(temporary, contents); fs.renameSync(temporary, file); }
  finally { if (fs.existsSync(temporary)) fs.unlinkSync(temporary); }
}
module.exports = function withPersonalQueue(config) {
  return withFinalizedMod(config, ['ios', async mod => {
    const root = mod.modRequest.platformProjectRoot;
    const project = require('xcode').project(path.join(root, 'ReelBot.xcodeproj/project.pbxproj'));
    project.parseSync();
    const targets = project.pbxNativeTargetSection();
    const main = Object.keys(targets).find(key => targets[key]?.name?.replaceAll('"', '') === 'ReelBot');
    const extension = Object.keys(targets).find(key => targets[key]?.name?.replaceAll('"', '') === 'ReelBotShareExtension');
    if (!extension || !main) throw new Error('Expected app and share extension targets.');
    for (const [file, target, folder] of [['QueueStore.swift', main, 'ReelBot'], ['ShareDiagnostics.swift', main, 'ReelBot'], ['ReelBotQueue.swift', main, 'ReelBot'], ['ReelBotQueue.m', main, 'ReelBot'], ['QueueStore.swift', extension, 'ReelBotShareExtension'], ['ShareDiagnostics.swift', extension, 'ReelBotShareExtension'], ['SharedURL.swift', extension, 'ReelBotShareExtension'], ['NativeMotion.swift', extension, 'ReelBotShareExtension']]) {
      const relative = `${folder}/${file}`;
      if (!project.hasFile(relative)) {
        const added = project.addSourceFile(relative, { target }, project.getFirstProject().firstProject.mainGroup);
        // The upstream extension uses a custom phase label; xcode's helper otherwise
        // falls back to the app's Sources phase. Assign by the target's actual phase.
        const sources = project.hash.project.objects.PBXSourcesBuildPhase;
        for (const phase of Object.values(sources)) if (phase.files) phase.files = phase.files.filter(entry => entry.value !== added.uuid);
        const destination = targets[target].buildPhases.find(phase => sources[phase.value]);
        sources[destination.value].files.push({ value: added.uuid, comment: `${file} in Sources` });
      }
    }
    // The upstream extension can attach its controller build file to both Sources phases.
    const sourcePhases = project.hash.project.objects.PBXSourcesBuildPhase;
    const extensionSources = targets[extension].buildPhases.find(phase => sourcePhases[phase.value]);
    const references = project.hash.project.objects.PBXFileReference;
    for (const [id, build] of Object.entries(project.hash.project.objects.PBXBuildFile)) {
      if (id.endsWith('_comment') || !build?.fileRef) continue;
      if (!String(references[build.fileRef]?.path || '').includes('ShareExtensionViewController.swift')) continue;
      for (const phase of Object.values(sourcePhases)) if (phase.files) phase.files = phase.files.filter(row => row.value !== id);
      sourcePhases[extensionSources.value].files.push({ value: id, comment: 'ShareExtensionViewController.swift in Sources' });
    }
    // Fresh Expo projects may not yet have the Resources group used by xcode's helper.
    if (!project.pbxGroupByName('Resources')) {
      const group = project.addPbxGroup([], 'Resources');
      project.getPBXGroupByKey(project.getFirstProject().firstProject.mainGroup).children.push({ value: group.uuid, comment: 'Resources' });
    }
    const fontFiles = ['Switzer-Regular.otf', 'Switzer-Semibold.otf'];
    for (const file of fontFiles) {
      const relative = '../assets/fonts/' + file;
      if (!project.hasFile(relative)) {
        const added = project.addResourceFile(relative, { target: extension }, project.getFirstProject().firstProject.mainGroup);
        const resources = project.hash.project.objects.PBXResourcesBuildPhase;
        for (const phase of Object.values(resources)) if (phase.files) phase.files = phase.files.filter(entry => entry.value !== added.uuid);
        const destination = targets[extension].buildPhases.find(phase => resources[phase.value]);
        resources[destination.value].files.push({ value: added.uuid, comment: file + ' in Resources' });
      }
    }
    const phases = project.hash.project.objects.PBXShellScriptBuildPhase;
    targets[extension].buildPhases = targets[extension].buildPhases.filter(phase => {
      const script = phases[phase.value];
      if (script && /Bundle React Native|Configure project|Start Packager/.test((script.name || '') + (script.shellScript || ''))) { delete phases[phase.value]; delete phases[phase.value + '_comment']; return false; }
      return true;
    });
    const configs = project.pbxXCBuildConfigurationSection();
    const lists = project.hash.project.objects.XCConfigurationList;
    const attributes = project.getFirstProject().firstProject.attributes;
    attributes.TargetAttributes ||= {};
    const mainSettings = configs[lists[targets[main].buildConfigurationList].buildConfigurations[0].value].buildSettings;
    for (const target of [main, extension]) {
      attributes.TargetAttributes[target] ||= {};
      attributes.TargetAttributes[target].SystemCapabilities ||= {};
      attributes.TargetAttributes[target].SystemCapabilities['com.apple.ApplicationGroups.iOS'] = { enabled: 1 };
      for (const entry of lists[targets[target].buildConfigurationList].buildConfigurations) {
        const settings = configs[entry.value].buildSettings;
        settings.IPHONEOS_DEPLOYMENT_TARGET = mainSettings.IPHONEOS_DEPLOYMENT_TARGET || '15.1';
        settings.PRODUCT_BUNDLE_IDENTIFIER = target === main ? config.ios.bundleIdentifier : `${config.ios.bundleIdentifier}.ShareExtension`;
        settings.CURRENT_PROJECT_VERSION = config.ios.buildNumber;
      }
    }
    const copyPhases = project.hash.project.objects.PBXCopyFilesBuildPhase;
    for (const reference of targets[main].buildPhases) {
      const phase = copyPhases[reference.value];
      if (phase && String(phase.dstSubfolderSpec) === '13' && phase.files?.length) {
        phase.name = '"Embed App Extensions"'; reference.comment = 'Embed App Extensions';
        copyPhases[reference.value + '_comment'] = 'Embed App Extensions';
      }
    }
    for (const entry of lists[targets[extension].buildConfigurationList].buildConfigurations) {
      const settings = configs[entry.value].buildSettings;
      settings.APPLICATION_EXTENSION_API_ONLY = 'YES';
      settings.SWIFT_VERSION = '5.0';
      settings.OTHER_LDFLAGS = ['"$(inherited)"'];
    }
    writeGenerated(path.join(root, 'ReelBot.xcodeproj/project.pbxproj'), project.writeSync());
    const native = path.join(mod.modRequest.projectRoot, 'native');
    for (const [folder, files] of [['ReelBot', ['QueueStore.swift', 'ShareDiagnostics.swift', 'ReelBotQueue.swift', 'ReelBotQueue.m']], ['ReelBotShareExtension', ['QueueStore.swift', 'ShareDiagnostics.swift', 'SharedURL.swift', 'NativeMotion.swift', 'ShareExtensionViewController.swift']]]) {
      fs.mkdirSync(path.join(root, folder), { recursive: true });
      for (const file of files) writeGenerated(path.join(root, folder, file), fs.readFileSync(path.join(native, file)));
    }
    const podfile = path.join(root, 'Podfile');
    const source = fs.readFileSync(podfile, 'utf8');
    const index = source.indexOf("target 'ReelBotShareExtension' do");
    if (index < 0) throw new Error('Expected generated extension Podfile target.');
    writeGenerated(podfile, source.slice(0, index) + "# Queue-only extension uses Foundation/UIKit and no Pods.\ntarget 'ReelBotShareExtension' do\nend\n");
    const mainInfoPath = path.join(root, 'ReelBot', 'Info.plist');
    const mainInfo = plist.parse(fs.readFileSync(mainInfoPath, 'utf8'));
    mainInfo.CFBundleVersion = config.ios.buildNumber;
    writeGenerated(mainInfoPath, plist.build(mainInfo));
    const entitlementPath = path.join(root, 'ReelBotShareExtension', 'ReelBotShareExtension.entitlements');
    const entitlements = plist.parse(fs.readFileSync(entitlementPath, 'utf8'));
    delete entitlements['com.apple.developer.applesignin'];
    entitlements['com.apple.security.application-groups'] = [config.extra.appGroupIdentifier];
    writeGenerated(entitlementPath, plist.build(entitlements));
    const infoPath = path.join(root, 'ReelBotShareExtension', 'Info.plist');
    const info = plist.parse(fs.readFileSync(infoPath, 'utf8'));
    info.UIAppFonts = fontFiles;
    info.AppGroupIdentifier = config.extra.appGroupIdentifier;
    info.AppGroup = config.extra.appGroupIdentifier;
    info.CFBundleDisplayName = 'ReelBot';
    info.CFBundleVersion = config.ios.buildNumber;
    info.NSExtension.NSExtensionAttributes.NSExtensionActivationRule = { NSExtensionActivationSupportsText: true, NSExtensionActivationSupportsWebURLWithMaxCount: 1 };
    writeGenerated(infoPath, plist.build(info));
    return mod;
  }]);
};
