/*
VibeCAD Installer Language File
Language: English
*/

!insertmacro LANGFILE_EXT "English"

${LangFileString} TEXT_INSTALL_CURRENTUSER "(Installed for Current User)"

${LangFileString} TEXT_WELCOME "This wizard will guide you through the installation of $(^NameDA). $\r$\n\
				$\r$\n\
				$_CLICK"

#${LangFileString} TEXT_CONFIGURE_PYTHON "Compiling Python scripts..."

${LangFileString} TEXT_FINISH_DESKTOP "Create desktop shortcut"
${LangFileString} TEXT_FINISH_WEBSITE "Visit github.com/10-X-eng/vibecad for the latest news, support and tips"

#${LangFileString} FileTypeTitle "VibeCAD-Document"

#${LangFileString} SecAllUsersTitle "Install for all users?"
${LangFileString} SecFileAssocTitle "File associations"
${LangFileString} SecDesktopTitle "Desktop icon"

${LangFileString} SecCoreDescription "The VibeCAD files."
#${LangFileString} SecAllUsersDescription "Install VibeCAD for all users or just the current user."
${LangFileString} SecFileAssocDescription "Files with a .FCStd extension will automatically open in VibeCAD."
${LangFileString} SecDesktopDescription "A VibeCAD icon on the desktop."
#${LangFileString} SecDictionaries "Dictionaries"
#${LangFileString} SecDictionariesDescription "Spell-checker dictionaries that can be downloaded and installed."

#${LangFileString} PathName 'Path to the file $\"xxx.exe$\"'
#${LangFileString} InvalidFolder 'The file $\"xxx.exe$\" is not in the specified path.'

#${LangFileString} DictionariesFailed 'Download of dictionary for language $\"$R3$\" failed.'

#${LangFileString} ConfigInfo "The following configuration of VibeCAD could take a while."

#${LangFileString} RunConfigureFailed "Could not run configure script."
${LangFileString} InstallRunning "The installer is already running!"
${LangFileString} UpgradeInstalled "$VibeCADInstalledDisplayVersion is installed.$\r$\n\
				This installer will update it to ${APP_VERSION} using a clean replacement of the program files.$\r$\n\
				Your documents and preferences will be preserved.$\r$\n$\r$\n\
				Continue with the update?"
${LangFileString} RepairInstalled "VibeCAD ${APP_VERSION} is already installed.$\r$\n\
				Would you like to repair it by cleanly replacing the program files?$\r$\n\
				Your documents and preferences will be preserved."
${LangFileString} DowngradeBlocked "A newer VibeCAD release is already installed:$\r$\n\
				$VibeCADInstalledDisplayVersion$\r$\n$\r$\n\
				This installer contains ${APP_VERSION} and will not downgrade it automatically.$\r$\n\
				Uninstall the newer release first if you intentionally need to downgrade."
${LangFileString} ReplaceUnknownInstalled "An existing VibeCAD installation was found:$\r$\n\
				$VibeCADInstalledDisplayVersion$\r$\n$\r$\n\
				Its complete version/build order cannot be determined. Continuing will cleanly replace its$\r$\n\
				program files with ${APP_VERSION}; documents and preferences will be preserved.$\r$\n$\r$\n\
				Continue?"
${LangFileString} InvalidExistingInstall "The registered VibeCAD installation directory is missing or invalid.$\r$\n\
				The installer stopped without changing it. Uninstall the damaged entry before reinstalling VibeCAD."
# Retained for compatibility with downstream installer extensions that may
# still reference the historical language identifiers.
${LangFileString} AlreadyInstalled "VibeCAD ${APP_SERIES_KEY2} is already installed."
${LangFileString} NewerInstalled "A newer VibeCAD installation already exists."

#${LangFileString} FinishPageMessage "Congratulations! VibeCAD has been installed successfully.$\r$\n\
#					$\r$\n\
#					(The first start of VibeCAD might take some seconds.)"
${LangFileString} FinishPageRun "Launch VibeCAD"

${LangFileString} UnNotInRegistryLabel "Unable to find VibeCAD in the registry.$\r$\n\
					Shortcuts on the desktop and in the Start Menu will not be removed."
${LangFileString} UnInstallRunning "You must close VibeCAD first!"
${LangFileString} UnNotAdminLabel "You must have administrator privileges to uninstall VibeCAD!"
${LangFileString} UnReallyRemoveLabel "Are you sure you want to completely remove VibeCAD and all of its components?"
${LangFileString} UnFreeCADPreferencesTitle 'VibeCAD$\'s user preferences'

#${LangFileString} SecUnProgDescription "Uninstalls xxx."
${LangFileString} SecUnPreferencesDescription 'Deletes VibeCAD$\'s configuration$\r$\n\
						(folder $\"$AppPre\username\$\r$\n\
						$AppSuff\$\r$\n\
						${APP_DIR_USERDATA}$\")$\r$\n\
						for you or for all users (if you are admin).'
${LangFileString} DialogUnPreferences 'You chose to delete the VibeCAD user configuration.$\r$\n\
						This will also delete all installed VibeCAD addons, and will affect the$\r$\n\
						preferences for all versions of VibeCAD.$\r$\n\
						Are you sure you want to proceed?'
${LangFileString} SecUnProgramFilesDescription "Uninstall VibeCAD and all of its components."
