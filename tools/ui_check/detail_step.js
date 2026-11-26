// ui_drive detail-step scenario for Simple Firearm Logbook. The fixture plants
// four owned firearms, including one NFA record, and one Sold firearm. Drives
// the real detail-page Previous and Next buttons through the current list
// order, disposition toggle, search, sort, and modal guard.
//
// Does not cover: the visual appearance of the controls, keyboard navigation,
// lightbox behavior, or data changes made by the Edit dialog.

export default async function detailStep(helpers) {
  const { click, type, evaluate, waitFor, check, screenshot, fixture } = helpers;
  const title = () => evaluate("document.getElementById('detailTitle').textContent");
  const detailOpen = "document.getElementById('detailWrap').style.display !== 'none'";
  const listOpen = "document.getElementById('listWrap').style.display !== 'none'";
  const next = "#detailStepButtons button[title='Next firearm']";
  const previous = "#detailStepButtons button[title='Previous firearm']";
  const titles = {
    beretta: "Beretta 92FS",
    colt: "Colt Python",
    glock: "Glock 19",
    ruger: "Ruger 10/22",
    sig: "Sig Sauer P320",
  };
  const ownedOrder = [titles.beretta, titles.colt, titles.glock, titles.sig];
  const row = key => `tr[onclick="openDetail(${fixture.ids[key]})"]`;

  async function showList() {
    if (await evaluate(detailOpen)) {
      await click("button[onclick='showListView()']");
      await waitFor(listOpen, 5000);
    }
  }

  async function open(key) {
    await showList();
    await waitFor(`document.querySelector('${row(key)}') !== null`, 5000);
    await click(row(key));
    await waitFor(detailOpen, 5000);
    await waitFor(`document.getElementById('detailTitle').textContent === ${JSON.stringify(titles[key])}`, 5000);
  }

  async function step(selector, expectedTitle) {
    await click(selector);
    await waitFor(`document.getElementById('detailTitle').textContent === ${JSON.stringify(expectedTitle)}`, 5000);
  }

  await waitFor("typeof api === 'function' && api() && typeof api().list_firearms === 'function'", 10000);
  check("the fixture supplied five firearm ids", !!fixture && fixture.ids && Object.keys(fixture.ids).length === 5,
    JSON.stringify(fixture));

  // a) Default log-number order is the fixture's creation order.
  await open("beretta");
  await step(next, titles.colt);
  check("Next from the first visible firearm opens the second by title", await title() === titles.colt, await title());
  await screenshot("detail-step-buttons");

  // b) Wrap forward from the last owned row.
  await open("sig");
  await step(next, titles.beretta);
  check("Next from the last visible firearm wraps to the first", await title() === titles.beretta, await title());

  // c) Wrap backward from the first owned row.
  await step(previous, titles.sig);
  check("Previous from the first visible firearm wraps to the last", await title() === titles.sig, await title());

  // d) Walk the owned list once with disposed rows hidden.
  await open("beretta");
  const visitedOwned = [await title()];
  for (let index = 1; index < ownedOrder.length; index += 1) {
    await step(next, ownedOrder[index]);
    visitedOwned.push(await title());
  }
  await step(next, ownedOrder[0]);
  check("with disposed firearms hidden, Next visits every owned firearm once and never Sold",
    JSON.stringify(visitedOwned) === JSON.stringify(ownedOrder) && !visitedOwned.includes(titles.ruger),
    JSON.stringify(visitedOwned));

  // e) Use the real disposed toggle, then confirm the Sold row is between
  // Glock and Sig Sauer in its default sorted position.
  await showList();
  await click("label.show-disposed-toggle");
  await waitFor(`document.querySelector('${row("ruger")}') !== null`, 5000);
  await open("beretta");
  const withDisposed = [await title()];
  for (const expected of [titles.colt, titles.glock, titles.ruger]) {
    await step(next, expected);
    withDisposed.push(await title());
  }
  check("with disposed firearms shown, the Sold firearm is visited in sorted place",
    JSON.stringify(withDisposed) === JSON.stringify([titles.beretta, titles.colt, titles.glock, titles.ruger]),
    JSON.stringify(withDisposed));

  // f) Click the Make column twice: once to select it, then again to reverse
  // it. Next must follow that reversed table order.
  await showList();
  await click("th[data-col='make']");
  await click("th[data-col='make']");
  await open("sig");
  await step(next, titles.ruger);
  check("after reversing the Make sort, Next follows the reversed order", await title() === titles.ruger, await title());

  // g) A one-result search leaves no other visible firearm to step to.
  await showList();
  await type("#searchInput", "Beretta");
  await waitFor(`document.querySelectorAll('#fwTableBody tr').length === 1`, 5000);
  await open("beretta");
  const buttonsHidden = await evaluate("document.getElementById('detailStepButtons').style.display === 'none'");
  check("a single-result search hides the Previous and Next buttons", buttonsHidden, String(buttonsHidden));

  // h) Clear the real search control, then verify an open Edit dialog prevents
  // a real Next-button click from changing the firearm. Close without saving.
  await showList();
  await click("#searchClear");
  await waitFor(`document.querySelectorAll('#fwTableBody tr').length === 5`, 5000);
  await open("sig");
  const beforeModalNext = await title();
  await click("button[onclick='openEditFirearmModal()']");
  await waitFor("document.getElementById('firearmModalScrim').classList.contains('open')", 5000);
  // The modal covers the button, so a real pointer cannot reach it (the
  // driver refuses the click). Send the click to the button itself to prove
  // the code's own guard also ignores it.
  await evaluate(`document.querySelector(${JSON.stringify(next)}).click(); true`);
  await new Promise(resolve => setTimeout(resolve, 300));
  const unchangedWithModal = await title() === beforeModalNext;
  await click("#firearmModalScrim button[onclick='closeFirearmModal()']");
  await waitFor("!document.getElementById('firearmModalScrim').classList.contains('open')", 5000);
  check("with the Edit modal open, clicking Next does not change the firearm", unchangedWithModal,
    `${beforeModalNext} -> ${await title()}`);
}
