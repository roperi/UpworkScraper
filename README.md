<h1 align="center">Upwork Scraper</h1>
<h2 align="center">Python + Selenium + SQLite</h2>

---

**UPDATE**: Still works as of February 2026.

Upwork Scraper is designed to automate the process of scraping job postings from **Upwork Best Matches**. It utilizes Selenium for web scraping and interacts with the Upwork website to extract job details, including job titles, descriptions, and proposals. The script then stores the extracted data in a SQLite database for easy access and retrieval.

The script provides a streamlined solution for users who want to efficiently search for new job opportunities on Upwork without the hassle of manually browsing through job listings.

---
## Benefits

- **Time Saving:** Automates the process of scraping job postings from Upwork, saving users time and effort.
- **Efficient Job Search:** Facilitates a more efficient job search experience by automatically collecting and organizing job details.
- **Customizable Interval:** Allows users to set the interval between scraping jobs according to their preferences.

## Key Features

- **Automated Scraping:** Automatically scrolls through job postings on Upwork and extracts relevant job details.
- **Database Integration:** Stores job details in a SQLite database for easy access and retrieval.


## Disclaimer

This script is designed to automate the process of scraping job postings from Upwork in order to streamline the job searching experience. It aims to save time and effort for individuals who regularly check for new job opportunities on the site.

While the script is intended to be a helpful tool, users should be aware that scraping data from Upwork may potentially violate Upwork's terms of service or other legal agreements. It is important to use this script responsibly and in accordance with all applicable laws and regulations.

The author of this script disclaims any liability for the misuse or improper use of the script. Users assume all risks associated with its use, including any legal consequences that may arise from violating Upwork's terms of service.

By using this script, you agree to use it responsibly and to comply with all applicable laws and regulations. The author encourages users to review Upwork's terms of service and other legal agreements before using this script.

Use this script at your own discretion and risk.

## Requirements

- Python 3.x
- Selenium
- Undetected Chromedriver
- SQLite3

## Tested Environment

This program has been tested and verified to work correctly in Python 3.11.

## Installation

1. **Create Virtual Environment:** It's recommended to create a virtual environment to isolate the dependencies of this project. You can create a virtual environment with Python 3.11 using the following command:

    ```bash
    python3 -m venv venv
    ```

    This command will create a virtual environment named `venv` in the current directory.

2. **Activate Virtual Environment:** After creating the virtual environment, activate it using the appropriate command for your operating system:

    - On Windows:

        ```bash
        .\venv\Scripts\activate
        ```

    - On macOS and Linux:

        ```bash
        source venv/bin/activate
        ```
      
3. **Clone the repository:**
   
    ```
    git clone https://github.com/roperi/UpworkScraper.git
    ```

4. **Navigate to the project directory:**
   
    ```
    cd UpworkScraper/
    ```

5. **Install the required dependencies:**
   
    ```
    pip install -r requirements.txt
    ```

## Configuration file

Create a folder named `settings` and add a __init__.py file inside it. 

```
settings/__init__.py
```

Inside the `settings` folder put a `config.py` file with the following content (adjust to your needs) and save the file.

```commandline
# Upwork credentials
UPWORK_USER_NAME = "John"
UPWORK_USERNAME = "john@doe.com"
UPWORK_PASSWORD = "p455w0rD"

# Chrome driver settings
CHROME_VERSIONS = [
    90,
    123,
]
MAX_ATTEMPTS = 3

```

**Configuration**
* UPWORK_USER_NAME: Replace `John` with the **first name** shown next to your profile picture on the right side panel. So if the name shown next to your profile pic says "John Smith" provide the script with `John`.
 Login and navigate to [Upwork Best Matches](https://www.upwork.com/nx/find-work/best-matches) to double-check what is the first name of your full name that appears on your profile description. 
* UPWORK_USERNAME: Your Upwork username or email.
* UPWORK_PASSWORD: Your Upwork password. 
* CHROME_VERSIONS: The Chrome versions installed in your system. No need to put the whole version number. So if your Chrome version is 90.0.4430.212, you just need to put 90 in the list. 
* MAX_ATTEMPTS: Max number of attempts Selenium will try to launch the Chromedriver.


### Error: from session not created: This version of ChromeDriver only supports Chrome version 96 # or what ever version

This is an [annoying bug](https://github.com/ultrafunkamsterdam/undetected-chromedriver) because you might encounter it at random times. Sometimes a Chrome version works, sometimes it doesn't. To get around this increase the MAX_ATTEMPTS value or try installing in your system and adding another Chrome version to the CHROME_VERSIONS list. Upwork Scraper will attempt to login with all versions available until it reaches max number of attempts.


## Usage

Run Upwork Scraper with the following command:

```bash
python upwork_best_matches_scraper.py
```

## Functionality
Upwork Scraper performs the following tasks:

1. Goes to the Upwork login page and logs you in.
2. Scrapes job postings from Upwork Best Matches page.
3. Parses job details and stores them in a SQLite database.

## Automate execution of script with a Cron job

You can launch Upwork scraper via a bash script as a cron job. 

### Bash script

Create a `launch_upwork_scraper.sh` file and put it inside a `bin` folder in the project directory.

```commandline
#!/bin/bash
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
parent="$(dirname "$DIR")"

# Activate virtualenv
source /path/to/your/venv/activate

# Run
nohup /path/to/your/venv/bin/python "$parent/upwork_best_matches_scraper.py" > /dev/null 2>&1 &
```
^ Edit the paths according to your virtual environment location.

Make the bash script executable:
```commandline
chmod +x bin/launch_upwork_scraper.sh
```

### Crontab

Edit your crontab to schedule the execution of the bash script. 

In the following example the scraper will be launched every 6 hours. So it should be run at minute 0 past every 6th hour. This means that the script will be run at 12:00 AM, 6:00 AM, 12:00 PM, and 6:00 PM every day

```commandline
0 */6 * * * /path/to/your/UpworkScraper/bin/launch_upwork_scraper.sh
```

You might have to add the following variables inside your crontab in order to launch the Chromedriver. See [here](https://stackoverflow.com/questions/50117377/selenium-with-chromedriver-doesnt-start-via-cron) for more info.
```commandline
SHELL=/bin/bash
PATH=/usr/local/bin:/usr/bin
DISPLAY=:0   
```

The values above are just examples. You need to replace them by finding your own values by issuing:
```
echo $SHELL
echo $PATH
env | grep "DISPLAY"
```

## TODO
- Capture job URLs ✅
- Capture job timestamp ✅
- Load more jobs when reaching the bottom of the page after scrolling down.


## Contributing
Contributions are welcome! If you encounter any issues or have suggestions for improvements, please open an issue or submit a pull request.


## Copyright and licenses
Copyright © 2024 roperi. 

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
