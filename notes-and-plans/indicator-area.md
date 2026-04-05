# What the indicator-area does
The idea of `indicator-area` is to clearly and succinctly communicates what is currently happening and if nothing is happening what last happened. So anytime the program is doing something that the user can not otherwise see, it should be displayed here. It should be very transparent what is happening at all times based on the `indicator-area`.

I don't want to try to make an exhaustive list of everything that should be displayed in it, but here are a few examples...
- when the user saves the file display something like "file saved to disk"
- when a tool call is made, it should be displayed here, for instance if the model edits a file it could say, "editing <file-name.txt>", if it is using web search, it could say, "searching for <query>", etc.
- tool results should be displayed too, such as, "edited <file-name.txt>", or "fetched page from github.com"
- any errors that occur should display here
- if the model is currently "thinking", it should show "thinking..."

Note: While the `indicator-area` should display enough information to let the user know what is happening at any point, its also important it doesn't just become a bunch of quickly flashing text, that the user can't meaningfully process, so some curation may be required, use your discretion and make reasonable choices