# Add OpenCanapy Menu/button

the idea is to create a button/menu that the user can use to customize OpenCanapy, OpenCore's Boot Picker, This design needs to have the following gaurds:

* No required resource can be empty. If it is use the default
* No invalid resource allowed (e.g, a video can't be the background)
* A size check, to make sure that the image will work


The backend needs to save to some way of saving the resources, maybe reinvent the data saving system, e.g make a custom file that will combine settings and assets, this way we can store per-user prefrences and have a option for admins to require there password to modify the settings, install root patches or install OpenCore. This way say the user is sharing their Mac with their friend who likes to explore and push buttons, they can lock the settings down.

The UI would have the following features:

* A `wx.ColorPicker` for the background color
* A drag-and-drop compatable/file picker import for a custom image background
* A gallery of images for the boot picker (ExHardDrive, Apple26, AppleRecv26, etc.)
* More built-in options for the user to choose from and the option for the user to upload their own
* A perview button, that generates a perview of the BootPicker, this way the user can see there ajustments.
* Font changer, the user can choose from built-in ones (provided by macOS) or upload there own (later)
* Label changer, (later, I don't have the reader for those files right now)

This should also change how the app ships it's resources, we don't need tons of `.icns` files as the image assets, they take up too much space. instead, we down grade them to `pngs`, and embed them in a custom `.assets` file at build-time, this is a `JSON` file that contains the image assets.

How this should be intagrated:

* the image embeder goes in `application.py`, recreating the `_embed_resources()` function
* create a new `gui_opencanapy.py` file for the main feature of this branch
* create the backend in `opencanapy_handler.py`
* intagrate the new settings system across `defaults.py` and `global_settings.py`
* add the lock option in the `General` section of `gui_settings.py`, ask for the user to unlock at launch, before the UI is rendered, this happens after the check for updates, which happen without authentication, if the User clicks `quit`, then the app quits. the dialog should state "Enter your password to open OpenCore Patcher T2. You can turn this off in App Settings -> Require Admin password after you enter your password" 

Order of fixes:

1. The icon system
2. The settings storage system
3. The "lockdown mode"
4. The OpenCanapy customization system


why this order?

because the icon system is the easiest to create, then the storage system because everything else would just need to be rewriten if I did it last, "lockdown mode" because it is somthing easy I can do before I work on the hardest part, the OpenCanapy customization system.


Steps for Step 1:

* convert all the icons to `png`
* change the `constants` lookup point to the new icons
* Test
* write the logic to make a python file with the `JSON` image assets
* Test
* write logic to add code to the python file that will make it embed the `JSON` dictionary into a `OpenCore-Patcher-T2.assets` file.
* Test
* write code to move the assets file into the `Resources` folder and delete the python file
* Test
* add `image_handler.py` to support, it finds out if it should get the image from the payloads dmg or from the `Resources` folder.
* change `constants.py` to send all it's image requests into `image_handler.py`
* Test, Test, Test


Steps for Step 2:

* use the map of the `.settings` file to make code that can create that file
* leave a option for the user to use the old dortania way of saving settings, but warn the user that certan features may not work, as they require the advanced pertection that the new system provides.
* make the file owned by `root`, this way standard users can't delete it to open the OpenCore-Patcher-T2 app
* Test
* Add a hash to check that the settings file hasn't been messed with.
* Test
* add `chown` recovery logic for the settings file
* Test by `chown`ing the file to your user and running the App normally

Steps for Step 3:

* add a checkbox in settings, have the warning be "Are you sure you want to continue? No one will be able to use OpenCore-Patcher-T2 without a admin Password!!"
* move the update check to before the `generate_elaments()`
* Test
* add a function that checks the locked down status and shows the admin dialog if needed, otherwise continues.
* Test

Steps for Step 4:

* start by adding the button to the OpenCore menu, create the nessisary placeholders to make it work
* add a dialog popup
* add a title
* Test
* start on the backend
* create a icns creater function, this needs to be able to create one out of genaric colors or `png`, `jpeg`, etc and have a peram for the name of the output e.g `GoldenGate/Apple26.icns`
* create a test script and Test
* create logic to load the custom or default OpenCanapy configeration
* Test with that test script
* add a function to write the config to the settings file, include placeholders for the data that is still set to the defaut or a built-in option
* Test multipy times
* create a graurdrail function
* Test the guard rail in mutiple instances
* move the all the functions in the backend into a `internal` class
* update the tests accordingly
* Test
* create a `OpenCanapyHandler` class
* add a function to that recieves a color or a image, if it is a image, it runs it though the corisponding gaurdrails, then if it passes, the image is sent to the icns creator function
* Test
* move the loader out of the `internal` class and into this one
* add a hardcoded constants location for the opencanapy resources
* create the UI
* Test, Test, Test and Test
* add a preview button, it generates a `Boot-Picker.png` of what all the icons would look like on the background

Steps for Optional Step 5:

* Setup a test to see if haveing the whole UI running on a thread will remove the need to restart the app to apply the Experimental mode changes, This could also unlock possiblities that where before imposible.
* change, Test, repeat